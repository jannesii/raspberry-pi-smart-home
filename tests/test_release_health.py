"""The running app and gateway report their release for deployment readiness.

``scripts/release.py`` reads these endpoints after starting a release (see
tests/test_release_deployment.py); they must work without a login and without
any device, notification or automation effect.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest
import redis
from flask import Flask
from sqlalchemy import create_engine

from app.blueprints.health import health_bp
from app.core.controller import Controller

ROOT = Path(__file__).resolve().parents[1]


def _app(ctrl) -> Flask:
    app = Flask(__name__)
    app.ctrl = ctrl  # type: ignore[attr-defined]
    app.register_blueprint(health_bp)
    return app


@pytest.fixture
def controller(tmp_path):
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    return ctrl


def test_health_reports_database_access_without_a_login(controller):
    response = _app(controller).test_client().get("/healthz")

    assert response.status_code == 200
    body = response.get_json()
    assert body["database"] == "ok"
    # A working checkout is not a prepared release.
    assert body["revision"] is None


def test_health_reports_unavailable_database_as_not_ready(controller, tmp_path):
    controller._sa_engine = create_engine(f"sqlite:///{tmp_path / 'missing' / 'app.db'}")

    response = _app(controller).test_client().get("/healthz")

    assert response.status_code == 503
    assert response.get_json()["database"] == "unavailable"


def test_health_without_a_configured_database_is_not_ready(controller):
    controller._sa_engine = None

    response = _app(controller).test_client().get("/healthz")

    assert response.status_code == 503
    assert response.get_json()["database"] == "unconfigured"


class _FakeRedis:
    def ping(self) -> None:
        return None


class _IdleThread:
    """The gateway's Redis subscriber is not started in this test."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


def test_gateway_health_reports_the_release_it_runs_from(tmp_path, monkeypatch):
    revision = "c0ffee" * 6 + "abcd"
    release = tmp_path / "releases" / revision[:12]
    (release / "code" / "esp32_ws").mkdir(parents=True)
    shutil.copy(ROOT / "esp32_ws" / "main.py", release / "code" / "esp32_ws" / "main.py")
    (release / "release.json").write_text(json.dumps({"revision": revision}))
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: _FakeRedis())
    monkeypatch.setattr(threading, "Thread", _IdleThread)
    spec = importlib.util.spec_from_file_location(
        "release_gateway_main", release / "code" / "esp32_ws" / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_gateway_main"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("release_gateway_main", None)

    response = module.app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"
    assert response.get_json()["revision"] == revision
