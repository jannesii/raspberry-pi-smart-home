"""Comfort heating and Heating preferences at the authenticated public seams."""

import pytest
import test_car_heater_sessions as evidence

from app.services.car_heater import KeepAtTempService
from app.services.car_heater.car_heater_service import CarHeaterService

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers


@pytest.fixture
def heater(session_app, monkeypatch):
    monkeypatch.setattr(
        "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    clock = [1800000000.0]
    svc = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    session_app.car_heater_service = svc
    session_app.keep_at_temp_service = KeepAtTempService(svc, session_app.ctrl)
    yield svc, clock
    svc.stop()


def ready(svc, relay_on=False, **extra):
    svc.observe_bounded_status(
        {
            "firmware_version": "3.1.1",
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": relay_on,
                "boot_id": "relay-one",
                "device_boot_id": "esp-one",
                **extra,
            },
        }
    )


def reload_comfort(app):
    """A restarted app-owned service reads only what was persisted."""
    app.keep_at_temp_service = KeepAtTempService(app.car_heater_service, app.ctrl)


def test_preferences_save_partially_and_survive_reload(heater, session_app, client):
    auth = headers(session_app, client)
    initial = client.get("/api/car_heater/comfort").get_json()
    assert initial["enabled"] is False
    assert initial["target_temperature_c"] is None
    assert initial["hysteresis_c"] is None

    saved = client.patch("/api/car_heater/comfort", json={"hysteresis_c": 1.5}, headers=auth)
    assert saved.status_code == 200
    assert saved.get_json()["hysteresis_c"] == 1.5
    saved = client.patch(
        "/api/car_heater/comfort", json={"target_temperature_c": -4.5}, headers=auth
    )
    assert saved.status_code == 200
    # Absent fields keep their saved values.
    assert saved.get_json()["hysteresis_c"] == 1.5

    reload_comfort(session_app)
    reloaded = client.get("/api/car_heater/comfort").get_json()
    assert reloaded["target_temperature_c"] == -4.5
    assert reloaded["hysteresis_c"] == 1.5
    assert reloaded["enabled"] is False


def test_start_is_refused_until_bounded_session_is_confirmed_off(heater, session_app, client):
    svc, _clock = heater
    auth = headers(session_app, client)
    client.patch(
        "/api/car_heater/comfort",
        json={"target_temperature_c": -5, "hysteresis_c": 1},
        headers=auth,
    )
    ready(svc)
    run = svc.start_bounded_session(60)

    state = client.get("/api/car_heater/comfort").get_json()
    assert state["start_available"] is False
    assert "confirmed OFF" in state["unavailable_reason"]
    refused = client.patch(
        "/api/car_heater/comfort",
        json={"enabled": True, "target_temperature_c": 2},
        headers=auth,
    )
    assert refused.status_code == 409
    assert "confirmed OFF" in refused.get_json()["error"]
    # A refused start saves nothing, including fields sent with it.
    reload_comfort(session_app)
    assert client.get("/api/car_heater/comfort").get_json()["target_temperature_c"] == -5
    # The refused start did not touch the owning session.
    assert svc.get_bounded_session()["phase"] == "starting"
    assert not any(c.get("source") == "keep_at_temp" for c in svc.peek_queued_commands())

    # Preferences stay editable; only starting is owned by the session.
    edited = client.patch("/api/car_heater/comfort", json={"hysteresis_c": 2}, headers=auth)
    assert edited.status_code == 200

    svc.stop_bounded_session()
    assert (
        client.patch("/api/car_heater/comfort", json={"enabled": True}, headers=auth).status_code
        == 409
    )
    ready(svc, session_id=run["session_id"], phase="stopped")
    started = client.patch("/api/car_heater/comfort", json={"enabled": True}, headers=auth)
    assert started.status_code == 200
    assert started.get_json()["enabled"] is True
    assert svc.get_bounded_session()["phase"] == "stopped"


def test_bounded_start_suspends_comfort_and_finish_never_revives_it(heater, session_app, client):
    svc, clock = heater
    auth = headers(session_app, client)
    client.patch(
        "/api/car_heater/comfort",
        json={"enabled": True, "target_temperature_c": -5, "hysteresis_c": 1},
        headers=auth,
    )
    # The same suspension app.services wires for every bounded start.
    svc.bind_mode_suspension(lambda: setattr(session_app.keep_at_temp_service, "enabled", False))
    ready(svc)
    run = svc.start_bounded_session(30)
    clock[0] += 1800
    svc.tick_bounded()
    ready(svc, session_id=run["session_id"], phase="stopped")
    reload_comfort(session_app)
    assert client.get("/api/car_heater/comfort").get_json()["enabled"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"target_temperature_c": 60},
        {"target_temperature_c": "warm"},
        {"hysteresis_c": 0},
        {"enabled": "yes"},
        {"enabled": True},  # no saved target or hysteresis yet
        {"charge_mode": True},
        {},
    ],
)
def test_invalid_changes_are_refused_and_save_nothing(heater, session_app, client, body):
    auth = headers(session_app, client)
    response = client.patch("/api/car_heater/comfort", json=body, headers=auth)
    assert response.status_code == 400
    assert response.get_json()["error"]
    reload_comfort(session_app)
    assert client.get("/api/car_heater/comfort").get_json() == {
        "enabled": False,
        "target_temperature_c": None,
        "hysteresis_c": None,
        "start_available": True,
        "unavailable_reason": None,
    }


def test_changes_require_csrf_and_an_administrator(heater, session_app, client):
    assert client.patch("/api/car_heater/comfort", json={"hysteresis_c": 1}).status_code == 400

    class Viewer:
        is_authenticated = True
        is_active = True
        is_anonymous = False
        is_admin = False

        def get_id(self):
            return "viewer"

    auth = headers(session_app, client)
    session_app.login_manager._user_callback = lambda uid: Viewer()
    refused = client.patch("/api/car_heater/comfort", json={"hysteresis_c": 1}, headers=auth)
    assert refused.status_code == 403
    assert client.get("/api/car_heater/comfort").get_json()["hysteresis_c"] is None


def test_failed_save_reports_failure_and_keeps_previous_values(
    heater, session_app, client, monkeypatch
):
    auth = headers(session_app, client)
    client.patch("/api/car_heater/comfort", json={"hysteresis_c": 1}, headers=auth)

    def unavailable(_settings):
        raise OSError("database unavailable")

    monkeypatch.setattr(session_app.ctrl, "save_keep_at_temp_settings", unavailable)
    failed = client.patch("/api/car_heater/comfort", json={"hysteresis_c": 3}, headers=auth)
    assert failed.status_code == 500
    assert "not saved" in failed.get_json()["error"]
    assert client.get("/api/car_heater/comfort").get_json()["hysteresis_c"] == 1


class RecordingHandler:
    def __init__(self):
        self.views = []
        self.errors = []
        self.socketio = self

    def emit(self, event, payload=None, **_kw):
        self.errors.append(payload)

    def emit_to_views(self, event, payload=None):
        self.views.append((event, payload))

    def flash(self, message, category="info"):
        self.errors.append({"message": message})


def socket_update(app, data):
    """The browser's keep_at_temp_settings event as the logged-in owner."""
    from flask_login import login_user
    from flask_wtf.csrf import generate_csrf

    from app.sockets.car_heater_handlers import handle_car_heater_keep_at_temp

    handler = RecordingHandler()
    with app.test_request_context():
        login_user(app.login_manager._user_callback("owner"))
        token = generate_csrf()
        handle_car_heater_keep_at_temp(
            handler, {**data, "csrf_token": token} if "csrf_token" not in data else data
        )
    return handler


def test_socket_updates_match_http_validation_ownership_and_results(heater, session_app, client):
    svc, _clock = heater
    saved = socket_update(session_app, {"target_temperature_c": -3, "hysteresis_c": 1})
    assert saved.errors == []
    assert saved.views[-1] == (
        "keep_at_temp_settings",
        client.get("/api/car_heater/comfort").get_json(),
    )
    assert saved.views[-1][1]["target_temperature_c"] == -3

    invalid = socket_update(session_app, {"hysteresis_c": 0})
    assert "between" in invalid.errors[-1]["message"]
    forged = socket_update(session_app, {"hysteresis_c": 2, "csrf_token": "forged"})
    assert forged.errors and forged.views == []

    ready(svc)
    svc.start_bounded_session(60)
    refused = socket_update(session_app, {"enabled": True})
    assert "confirmed OFF" in refused.errors[-1]["message"]
    # Refusals re-send the saved state so the page drops its unsaved toggle.
    assert refused.views[-1][1]["enabled"] is False
    assert refused.views[-1][1]["start_available"] is False

    reload_comfort(session_app)
    assert client.get("/api/car_heater/comfort").get_json()["hysteresis_c"] == 1
