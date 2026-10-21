"""Public Diagnostics contracts with real services and isolated persistence."""

from datetime import UTC, datetime

import pytest
import test_car_heater_sessions as sessions
from test_car_heater_sensors import _DummySIOHandler, _DummySocketIO

from app.blueprints.api.car_heater import status as car_status
from app.core.models import CarHeaterSensorFrame, CarHeaterStatus
from app.services.car_heater.car_heater_service import CarHeaterService
from app.services.esp32_redis_bridge import ESP32RedisBridge

client = sessions.client
session_app = sessions.session_app
headers = sessions.headers


@pytest.fixture
def diagnostics(session_app, monkeypatch):
    monkeypatch.setattr(
        "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("isolated"))
    )
    service = CarHeaterService(session_app.ctrl)
    session_app.car_heater_service = service
    session_app.sio_handler = _DummySIOHandler()
    now = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)
    monkeypatch.setattr(car_status, "_utc_now", lambda: now)
    yield session_app
    service.stop()


@pytest.mark.parametrize("transport", ["http", "ws"])
@pytest.mark.parametrize(
    "result, expected",
    [(True, "success"), (False, "failed"), (None, "unknown"), ("false", "unknown")],
)
def test_restart_stages_require_explicit_device_ack(
    diagnostics, client, transport, result, expected
):
    auth = headers(diagnostics, client)
    assert (
        client.post(
            "/api/car_heater/queue", json={"action": "esp_restart"}, headers=auth
        ).status_code
        == 200
    )
    assert client.get("/api/car_heater/status").json["command_status"]["esp_restart"] == "queued"
    with diagnostics.app_context():
        _, code = car_status.handle_status_update_request(
            {"timestamp": "2026-10-21 21:30:00", "shelly_connected": False}, commands_enabled=True
        )
    assert code == 200
    assert client.get("/api/car_heater/status").json["command_status"]["esp_restart"] == "sent"
    ack = {"action": "esp_restart"}
    if result is not None:
        ack["success"] = result
    frame = {
        "timestamp": "2026-10-21 21:30:00",
        "shelly_connected": False,
        "action_results": [ack],
    }
    if transport == "http":
        with diagnostics.app_context():
            _, code = car_status.handle_status_update_request(frame, commands_enabled=True)
        assert code == 200
    else:
        ESP32RedisBridge(
            app=diagnostics, socketio=_DummySocketIO(), sio_handler=diagnostics.sio_handler
        )._emit_status(frame)
    assert client.get("/api/car_heater/status").json["command_status"]["esp_restart"] == expected


def test_diagnostics_mutations_and_history_require_auth_and_csrf(diagnostics, client):
    assert client.post("/api/car_heater/queue", json={"action": "esp_restart"}).status_code == 400
    anonymous = diagnostics.test_client()
    for path in ("/car_heater/settings", "/api/car_heater/history", "/api/car_heater/status"):
        assert anonymous.get(path).status_code in (302, 401)


def seed_history(ctrl):
    """Permanent samples around Helsinki midnight, with a failed read and a gap."""
    for minute, state, value in [
        (0, "ok", 0),
        (1, "invalid", None),
        (2, "ok", 2),
        (10, "stale", 3),
    ]:
        stamp = f"2026-10-20T21:{30 + minute:02d}:00+00:00"
        ctrl.record_car_heater_sensor_frame(
            CarHeaterSensorFrame(
                id=None,
                received_at=stamp,
                glass_reference_state=state,
                glass_reference_temp_c=value,
                glass_reference_sampled_at=stamp if value is not None else None,
                glass_comparison_state="ok",
                glass_comparison_temp_c=-1,
                glass_comparison_sampled_at=stamp,
                cabin_state="ok",
                cabin_temp_c=4,
                cabin_humidity_pct=0,
                cabin_sampled_at=stamp,
            )
        )
        ctrl.record_car_heater_status(
            CarHeaterStatus(
                id=None,
                timestamp=f"2026-10-21 00:{30 + minute:02d}:00+03:00",
                is_heater_on=False,
                instant_power_w=0,
                voltage_v=230,
                current_a=0,
                energy_total_wh=100,
                energy_last_min_wh=0,
                energy_ts=None,
                device_temp_c=15,
                device_temp_f=None,
                ambient_temp=None,
                source="HTTP",
            )
        )


def test_persisted_detailed_history_preserves_failed_reads_zero_and_helsinki_day(
    diagnostics, client
):
    seed_history(diagnostics.ctrl)
    data = client.get("/api/car_heater/history?date=2026-10-21").json
    assert datetime.fromisoformat(data["start"]) == datetime(2026, 10, 20, 21, tzinfo=UTC)
    assert datetime.fromisoformat(data["end"]) == datetime(2026, 10, 21, 21, tzinfo=UTC)
    assert [p["temperature_c"] for p in data["sensor_series"]["glass_reference"]] == [0, None, 2, 3]
    assert data["sensor_series"]["glass_reference"][1]["state"] == "invalid"
    assert data["sensor_series"]["glass_reference"][-1]["state"] == "stale"
    assert all(p["humidity_pct"] == 0 for p in data["sensor_series"]["cabin"])
    assert all(row["instant_power_w"] == 0 for row in data["rows"])
    assert data["energy_today_wh"] == 0
    assert (
        client.get("/api/car_heater/history?date=2026-10-20").json["sensor_series"]["cabin"] == []
    )


@pytest.mark.parametrize("transport", ["http", "ws"])
def test_logs_are_retrievable_during_browser_http_fallback(diagnostics, client, transport):
    frame = {"timestamp": "2026-10-21 21:30:00", "shelly_connected": False, "logs": "one\ntwo"}
    if transport == "http":
        with diagnostics.app_context():
            _, code = car_status.handle_status_update_request(frame, commands_enabled=True)
        assert code == 200
    else:
        ESP32RedisBridge(
            app=diagnostics, socketio=_DummySocketIO(), sio_handler=diagnostics.sio_handler
        )._emit_status(frame)
    data = client.get("/api/car_heater/status").json
    assert data["logs_snapshot"]["logs"] == "one\ntwo"
    assert data["logs_snapshot"]["source"] == f"{transport}_status"
    assert data["logs_snapshot"]["received_ts"]


@pytest.mark.parametrize("day, hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_history_date_boundaries_follow_helsinki_clock_changes(diagnostics, client, day, hours):
    response = client.get(f"/api/car_heater/history?date={day}")
    assert response.status_code == 200
    data = response.json
    assert (
        datetime.fromisoformat(data["end"]) - datetime.fromisoformat(data["start"])
    ).total_seconds() == hours * 3600


def test_restart_socket_queue_requires_auth_and_csrf_and_only_reports_intent(diagnostics, client):
    from flask_login import login_user
    from flask_wtf.csrf import generate_csrf
    from test_car_heater_comfort import RecordingHandler

    from app.sockets.car_heater_handlers import handle_car_heater_control

    with diagnostics.test_request_context():
        handler = RecordingHandler()
        handle_car_heater_control(handler, {"action": "esp_restart", "csrf_token": generate_csrf()})
        assert handler.errors and not handler.views
    assert client.get("/api/car_heater/status").json["command_status"]["esp_restart"] is None
    with diagnostics.test_request_context():
        login_user(diagnostics.login_manager._user_callback("owner"))
        forged = RecordingHandler()
        handle_car_heater_control(forged, {"action": "esp_restart", "csrf_token": "forged"})
        assert forged.errors and not forged.views
        accepted = RecordingHandler()
        handle_car_heater_control(
            accepted, {"action": "esp_restart", "csrf_token": generate_csrf()}
        )
        assert not accepted.errors
        assert accepted.views[-1][1]["stage"] == "queued"
    assert client.get("/api/car_heater/status").json["command_status"]["esp_restart"] == "queued"
