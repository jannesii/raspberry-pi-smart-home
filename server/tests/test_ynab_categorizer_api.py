from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask, jsonify
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import CSRFProtect, generate_csrf

from app.blueprints.api.ynab_categorizer_api import ynab_categorizer_bp


class _User(UserMixin):
    def __init__(self, username: str):
        self.id = username


class _CtrlStub:
    def get_user_by_username(self, username: str, include_pw: bool = False):
        if username == "root":
            return SimpleNamespace(is_root_admin=True)
        if username == "admin":
            return SimpleNamespace(is_root_admin=False)
        return None


class _ServiceStub:
    def __init__(self):
        self.apply_payloads = []

    def is_configured(self):
        return True

    def get_queue(self, queue_filter_mode=None):
        return {
            "queue_filter_mode": queue_filter_mode or "strict",
            "group_count": 0,
            "transaction_count": 0,
            "categories": [],
            "groups": [],
        }

    def apply_category(self, transaction_ids, category_id, applied_by_username=None):
        self.apply_payloads.append(
            {
                "transaction_ids": transaction_ids,
                "category_id": category_id,
                "applied_by_username": applied_by_username,
            }
        )
        return {
            "transaction_count": len(transaction_ids),
            "inserted_events": len(transaction_ids),
            "skipped_existing": 0,
        }

    def bootstrap(self, force=False):
        return {"skipped": False, "seeded_rows": 1, "force": force}

    def get_bootstrap_status(self):
        return {"bootstrapped": False, "state": None}

    def get_config(self):
        return {"queue_filter_mode": "strict", "updated_ts": "2026-03-09T10:00:00+02:00"}

    def set_queue_filter_mode(self, mode):
        return {"queue_filter_mode": mode, "updated_ts": "2026-03-09T10:00:00+02:00"}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": True,
            "TESTING": True,
        }
    )

    csrf = CSRFProtect()
    csrf.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return _User(user_id)

    @app.route("/csrf-token")
    def csrf_token_route():
        return jsonify({"csrf_token": generate_csrf()})

    app.ctrl = _CtrlStub()  # type: ignore[attr-defined]
    app.ynab_categorizer_service = _ServiceStub()  # type: ignore[attr-defined]
    api_root = Blueprint("api_root", __name__, url_prefix="/api")
    api_root.register_blueprint(ynab_categorizer_bp)
    app.register_blueprint(api_root)

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def _csrf_token(client) -> str:
    response = client.get("/csrf-token")
    payload = response.get_json() or {}
    return payload.get("csrf_token")


def test_queue_requires_root_admin(client):
    _login(client, "admin")
    response = client.get("/api/ynab-categorizer/queue")
    assert response.status_code == 403


def test_queue_root_admin_ok(client):
    _login(client, "root")
    response = client.get("/api/ynab-categorizer/queue")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True


def test_apply_post_requires_csrf(client):
    _login(client, "root")
    response = client.post(
        "/api/ynab-categorizer/apply",
        json={"transaction_ids": ["tx1"], "category_id": "cat1"},
    )
    assert response.status_code == 400


def test_apply_post_with_csrf_and_root_admin(client, app):
    _login(client, "root")
    token = _csrf_token(client)

    response = client.post(
        "/api/ynab-categorizer/apply",
        json={"transaction_ids": ["tx1", "tx2"], "category_id": "cat1"},
        headers={"X-CSRFToken": token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["transaction_count"] == 2

    svc = app.ynab_categorizer_service
    assert svc.apply_payloads[0]["transaction_ids"] == ["tx1", "tx2"]
    assert svc.apply_payloads[0]["category_id"] == "cat1"
    assert svc.apply_payloads[0]["applied_by_username"] == "root"


def test_not_configured_returns_503(client, app):
    _login(client, "root")
    app.ynab_categorizer_service = None  # type: ignore[attr-defined]

    response = client.get("/api/ynab-categorizer/queue")
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"] == "not_configured"
