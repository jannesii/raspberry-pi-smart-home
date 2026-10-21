"""Bounded heating at the public service and authenticated HTTP seams."""

import pytest
import test_car_heater_sessions as evidence

from app.services.car_heater.car_heater_service import CarHeaterService

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers


@pytest.fixture
def service(session_app, monkeypatch):
    monkeypatch.setattr(
        "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    clock = [1800000000.0]
    svc = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    session_app.car_heater_service = svc
    yield svc, clock
    svc.stop()


def ready(svc):
    svc.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "boot_id": "relay-one",
                "device_boot_id": "esp-one",
            }
        }
    )


def test_manual_start_has_permanent_identity_and_original_finish(service, session_app):
    svc, _clock = service
    ready(svc)
    run = svc.start_bounded_session(60)
    assert run["finish_at"] == 1800003600
    assert session_app.ctrl.get_car_heater_session(run["session_id"]).trigger == "manual"
    cmd = svc.get_queued_commands()[0]
    assert cmd["session_id"] == run["session_id"]
    assert cmd["finish_at"] == 1800003600
    assert cmd["expires_at"] == 1800000030
    with pytest.raises(ValueError, match="confirmed stopped"):
        svc.start_bounded_session(180)
    assert svc.get_bounded_session()["finish_at"] == 1800003600


def test_session_reports_mode_and_remaining_original_allowance(service, client):
    svc, clock = service
    ready(svc)
    svc.start_bounded_session(45)
    clock[0] += 600.4
    run = client.get("/api/car_heater/bounded").get_json()
    assert run["mode"] == "manual"
    assert run["remaining_s"] == 2099
    clock[0] += 3600
    assert client.get("/api/car_heater/bounded").get_json()["remaining_s"] == 0


def test_weather_conditions_carry_observation_time_over_http_and_websocket(session_app, client):
    from app.services.weather.weather_service import WeatherData, WeatherValue
    from app.sockets.car_heater_handlers import emit_car_heater_status_to_views

    observed = "2026-10-21T03:50:00Z"

    class Weather:
        def get_latest(self):
            return WeatherData(
                station_name="Pelmaa",
                fmisid="100968",
                lat=None,
                lon=None,
                t2m=WeatherValue(0.0, observed),
                ws_10min=None,
                rh=WeatherValue(91.0, observed),
                n_man=None,
            )

    class Views:
        def __init__(self):
            self.events = []

        def emit(self, event, payload):
            self.events.append(payload)

    session_app.weather_service = Weather()
    expected = {
        "outside_temp_c": 0.0,
        "wind_speed_mps": None,
        "humidity_pct": 91.0,
        "station_name": "Pelmaa",
        "observed_at": observed,
    }
    assert client.get("/api/car_heater/status").get_json()["weather"] == expected
    views = Views()
    with session_app.app_context():
        emit_car_heater_status_to_views(views, {"timestamp": observed}, None, None)
    assert views.events[0]["weather"] == expected


def test_device_report_age_is_judged_by_server_receipt_not_device_clock(
    session_app, client, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from test_car_heater_sensors import _DummySIOHandler

    from app.blueprints.api.car_heater import status as car_status

    now = [datetime(2026, 10, 21, 4, 0, tzinfo=UTC)]
    monkeypatch.setattr(car_status, "_utc_now", lambda: now[0])
    session_app.sio_handler = _DummySIOHandler()
    # The device clock runs ten minutes fast; receipt time is what counts.
    frame = {
        "timestamp": (now[0] + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
        "shelly_connected": True,
        "shelly": '{"output": false, "apower": 0}',
    }
    with session_app.app_context():
        car_status.handle_status_update_request(frame, commands_enabled=False)
    now[0] += timedelta(seconds=45)
    status = client.get("/api/car_heater/status").get_json()["status"]
    assert status["report_age_s"] == 45
    assert status["is_heater_on"] is False


@pytest.mark.parametrize("duration", [0, -1, 181, True, "60", None, float("inf")])
def test_invalid_manual_duration_is_rejected(service, duration):
    svc, _ = service
    ready(svc)
    with pytest.raises(ValueError):
        svc.start_bounded_session(duration)
    assert svc.get_queued_commands() == []


def test_unknown_capability_and_stale_status_block_start(service):
    svc, clock = service
    with pytest.raises(ValueError, match="capability"):
        svc.start_bounded_session()
    ready(svc)
    clock[0] += 31
    with pytest.raises(ValueError, match="capability"):
        svc.start_bounded_session()


def test_cancel_retries_until_correlated_relay_off_and_preserves_history(service, session_app):
    svc, _clock = service
    ready(svc)
    run = svc.start_bounded_session(180)
    svc.stop_bounded_session()
    assert [c["action"] for c in svc.get_queued_commands()] == ["turn_off"]
    svc.mark_command_success(
        [{"action": "turn_off", "session_id": run["session_id"], "success": True}]
    )
    assert svc.get_bounded_session()["off_state"] == "pending"
    with pytest.raises(ValueError, match="confirmed stopped"):
        svc.turn_on("keep_at_temp", "competing")
    assert svc.get_queued_commands() == []
    _clock[0] += 5
    assert svc.get_queued_commands()[0]["action"] == "turn_off"
    svc.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "session_id": "old",
                "relay_on": False,
                "phase": "stopped",
            }
        }
    )
    assert svc.get_bounded_session()["off_state"] == "pending"
    svc.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "session_id": run["session_id"],
                "relay_on": False,
                "phase": "stopped",
            }
        }
    )
    assert svc.get_bounded_session()["off_state"] == "confirmed"
    assert session_app.ctrl.get_car_heater_execution(run["session_id"])["phase"] == "stopped"


def test_expiry_and_restart_never_requeue_on_or_extend_finish(service, session_app):
    svc, clock = service
    ready(svc)
    run = svc.start_bounded_session(60)
    clock[0] += 3600
    assert [c["action"] for c in svc.get_queued_commands()] == ["turn_off"]
    svc.stop()
    restarted = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    try:
        assert restarted.get_bounded_session()["finish_at"] == run["finish_at"]
        assert restarted.get_bounded_session()["relay_on"] is None
        assert [c["action"] for c in restarted.get_queued_commands()] == ["turn_off"]
    finally:
        restarted.stop()


def test_authenticated_csrf_protected_manual_start_stop(service, session_app, client):
    svc, _ = service
    ready(svc)
    endpoint = "/api/car_heater/bounded"
    auth = headers(session_app, client)
    assert session_app.test_client().get(endpoint).status_code == 401
    assert client.post(endpoint, json={"duration_minutes": 60}).status_code == 400
    response = client.post(endpoint, json={"duration_minutes": 60}, headers=auth)
    assert response.status_code == 201
    run = response.get_json()
    assert client.post(endpoint, json={"duration_minutes": 60}, headers=auth).status_code == 409
    assert client.delete(endpoint, headers=auth).get_json()["off_state"] == "pending"
    assert client.get(endpoint).get_json()["session_id"] == run["session_id"]


def test_expired_start_requests_off_even_without_execution_confirmation(service):
    svc, clock = service
    ready(svc)
    svc.start_bounded_session(60)
    clock[0] += 31
    svc.tick_bounded()
    assert [c["action"] for c in svc.get_queued_commands()] == ["turn_off"]
    assert svc.get_bounded_session()["off_state"] == "pending"


def test_failed_off_and_missing_status_remain_unknown(service):
    svc, _ = service
    ready(svc)
    run = svc.start_bounded_session()
    svc.stop_bounded_session()
    svc.mark_command_success(
        [{"action": "turn_off", "session_id": run["session_id"], "success": False}]
    )
    svc.observe_bounded_status({"shelly_connected": False})
    assert svc.get_bounded_session()["off_state"] == "failed_or_unknown"
    assert svc.get_bounded_session()["relay_on"] is None
    assert [c["action"] for c in svc.get_queued_commands()] == ["turn_off"]


def test_modes_cannot_replace_pending_session_at_public_service_boundaries(service, session_app):
    from datetime import UTC, datetime

    from app.services.car_heater.keep_at_temp_service import KeepAtTempService, KeepAtTempSettings
    from app.services.car_heater.ready_by_service import ReadyByService

    svc, _ = service
    ready(svc)
    comfort = KeepAtTempService(svc, session_app.ctrl)
    ready_by = ReadyByService(
        car_heater_service=svc,
        kfactor_calibrator=None,
        keep_at_temp_service=comfort,
        ctrl=session_app.ctrl,
    )
    svc.start_bounded_session()
    with pytest.raises(ValueError, match="confirmed stopped"):
        comfort.update_settings(KeepAtTempSettings(enabled=True))
    with pytest.raises(ValueError, match="confirmed stopped"):
        ready_by.schedule(ready_by_ts=datetime.now(UTC), target_temp_c=20)
    with pytest.raises(ValueError, match="confirmed stopped"):
        svc.queue_command({"action": "turn_on", "source": "calibration"})


def test_execution_detail_shows_confirmed_power_separately_from_start(service, session_app, client):
    svc, _ = service
    ready(svc)
    run = svc.start_bounded_session()
    svc.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "session_id": run["session_id"],
                "relay_on": True,
                "power_w": 1400,
                "phase": "heating",
            }
        }
    )
    detail = client.get("/api/car_heater/sessions/" + run["session_id"]).get_json()
    assert detail["execution"]["phase"] == "heating"
    assert [m["kind"] for m in detail["execution"]["milestones"]] == [
        "start_requested",
        "relay_on",
        "powered_on",
    ]
    assert detail["execution"]["power_w"] == 1400


def test_unrelated_telemetry_cannot_refresh_active_relay_or_power(service):
    svc, clock = service
    ready(svc)
    run = svc.start_bounded_session()
    svc.observe_bounded_status(
        {"bounded": {"session_id": run["session_id"], "relay_on": True, "power_w": 1400}}
    )
    clock[0] += 20
    svc.observe_bounded_status(
        {"bounded": {"session_id": "other", "relay_on": False, "power_w": 0}}
    )
    clock[0] += 20
    assert svc.get_bounded_session()["relay_on"] is None
    assert svc.get_bounded_session()["power_w"] is None


def test_explicit_capability_loss_clears_live_power_without_erasing_milestones(service):
    svc, _ = service
    ready(svc)
    run = svc.start_bounded_session()
    svc.observe_bounded_status(
        {"bounded": {"session_id": run["session_id"], "relay_on": True, "power_w": 1400}}
    )
    svc.observe_bounded_status({"shelly_connected": False})
    assert svc.get_bounded_session()["power_w"] is None
    assert any(m["kind"] == "powered_on" for m in svc.get_bounded_session()["milestones"])


def test_old_acknowledgements_do_not_confirm_new_commands(service):
    svc, _ = service
    ready(svc)
    svc.start_bounded_session()
    svc.mark_command_success([{"action": "turn_on", "session_id": "old-session", "success": True}])
    assert svc.get_command_status().turn_on == "queued"


def test_failed_off_waits_before_retry_and_keeps_original_finish(service):
    svc, clock = service
    ready(svc)
    run = svc.start_bounded_session()
    svc.stop_bounded_session()
    assert [c["action"] for c in svc.get_queued_commands()] == ["turn_off"]
    svc.mark_command_success(
        [{"action": "turn_off", "session_id": run["session_id"], "success": False}]
    )
    assert svc.get_queued_commands() == []
    clock[0] += 5
    retry = svc.get_queued_commands()
    assert [c["action"] for c in retry] == ["turn_off"]
    assert retry[0]["finish_at"] == run["finish_at"]
