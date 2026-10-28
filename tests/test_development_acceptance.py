"""Operator acceptance checks never initialize application integrations."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_development_launch import (  # noqa: F401
    free_port,
    postgres_socket_dir,
    redis_pair,
    write_env,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def operator(tmp_path, request):
    pg_socket = request.getfixturevalue("postgres_socket_dir")
    isolated_redis = request.getfixturevalue("redis_pair")
    # This launcher copy imports real policy but cannot initialize an app even
    # when a regression lets the launch proceed. No production endpoints exist.
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/dev.py", checkout / "scripts/dev.py")
    (checkout / "app").symlink_to(ROOT / "app")
    (checkout / "run.py").write_text(
        'import os\nprint("ANNOUNCEMENTS_ENABLED=" + os.environ.get("ANNOUNCEMENTS_ENABLED", "unset"), flush=True)\nraise RuntimeError("SAFE_TEST_SENTINEL")\n'
    )
    state = tmp_path / "state"
    state.mkdir()
    (state / ".jannenkoti-development").write_text("test state")
    values = {
        "DATABASE_URL": f"postgresql+psycopg:///acceptance?host={pg_socket}",
        "REDIS_URL": isolated_redis["development"][2],
        "DEV_STATE_DIR": str(state),
        "DB_PATH": str(state / "legacy.db"),
        "VILLENKOTI_DB_PATH": str(state / "other.db"),
        "SECRET_KEY": "synthetic-dev",
        "WEB_USERNAME": "tester",
        "WEB_PASSWORD": "synthetic-dev-password",
        "PORT": str(free_port()),
        "ANNOUNCEMENTS_ENABLED": "true",
    }
    production = write_env(
        tmp_path / "production.env",
        {
            "DATABASE_URL": f"postgresql+psycopg://prod@/production?host={pg_socket}",
            "REDIS_URL": isolated_redis["production"][2],
            "SECRET_KEY": "synthetic-prod",
            "WEB_PASSWORD": "synthetic-prod-password",
        },
    )

    def invoke(selection, command="run"):
        values["DEV_ACCEPTANCE_MEDIA_ORIGIN"] = f"http://127.0.0.1:{free_port()}"
        values["DEV_ACCEPTANCE"] = json.dumps(selection)
        env = write_env(tmp_path / "dev.env", values)
        # Master exits promptly if the sentinel is reached. Bound worker retries.
        proc = subprocess.Popen(
            [
                sys.executable,
                str(checkout / "scripts/dev.py"),
                command,
                "--env-file",
                str(env),
                "--production-env",
                str(production),
            ],
            cwd=checkout,
            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        (tmp_path / "operator.log").write_text(stdout + stderr)
        return proc.returncode, stdout + stderr

    return invoke


def test_unknown_acceptance_integration_is_refused_before_launch(operator):
    code, output = operator({"heater": {"host": "192.0.2.1"}})
    assert code == 2, output
    assert "DEV_ACCEPTANCE" in output and "unknown integration" in output
    assert "SAFE_TEST_SENTINEL" not in output


def test_operator_reports_selected_destinations_without_webhook_secret(operator):
    code, output = operator(
        {
            "speakers": [{"key": "test", "name": "Test", "host": "127.0.0.1"}],
            "discord": {
                "label": "Acceptance channel",
                "webhook": "https://discord.com/api/webhooks/123/synthetic-secret",
            },
        },
        "status",
    )
    assert code == 3, output
    assert "speaker test (127.0.0.1:8009)" in output
    assert "Discord Acceptance channel" in output
    assert "synthetic-secret" not in output
    assert "discord.com" not in output


def test_operator_can_return_to_safe_development(operator):
    code, output = operator({}, "status")
    assert code == 3, output
    assert "off (safe development)" in output


def test_launcher_forces_candidate_production_builder_off(operator):
    code, output = operator({})
    assert code == 3, output
    assert "SAFE_TEST_SENTINEL" in output
    assert "ANNOUNCEMENTS_ENABLED=false" in output
