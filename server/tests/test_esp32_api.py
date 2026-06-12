from __future__ import annotations

from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin

from app.blueprints.api.esp32_api import esp32_bp, process_esp32_temphum_payload


class _User(UserMixin):
    def __init__(self, username: str):
        self.id = username


class _CtrlStub:
    def __init__(self):
        self.locations = [
            {
                "location": "Kitchen",
                "temperature": 21.5,
                "humidity": 42.0,
                "timestamp": "2026-05-04T12:00:00",
            }
        ]

    def get_unique_locations(self):
        return self.locations


def _create_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)

    login_manager = LoginManager()
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return _User(user_id)

    app.ctrl = _CtrlStub()  # type: ignore[attr-defined]
    api_root = Blueprint("api_root", __name__, url_prefix="/api")
    api_root.register_blueprint(esp32_bp)
    app.register_blueprint(api_root)
    return app


def _login(client, username: str):
    with client.session_transaction() as session:
        session["_user_id"] = username
        session["_fresh"] = True


def test_latest_esp32_temphum_returns_latest_location_snapshot():
    app = _create_app()
    client = app.test_client()
    _login(client, "viewer")

    response = client.get("/api/esp32_temphum/latest")

    assert response.status_code == 200
    assert response.get_json() == app.ctrl.locations


def test_invalid_temphum_logs_only_none_field_names(caplog):
    app = _create_app()
    data = {
        "location": "WC",
        "temperature_c": None,
        "humidity_pct": None,
        "large_payload_value": "must-not-be-logged",
    }

    with app.app_context(), caplog.at_level("WARNING"):
        payload, status_code = process_esp32_temphum_payload(data, source="ws")

    assert status_code == 400
    assert payload["error"] == "invalid_payload"
    assert "Bad esp32 temphum payload; None fields: temperature_c, humidity_pct" in caplog.text
    assert "must-not-be-logged" not in caplog.text
