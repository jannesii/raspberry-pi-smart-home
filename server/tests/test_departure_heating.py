"""Departure planning through public scheduling, telemetry and session seams."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import test_car_heater_sessions as evidence

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers


@pytest.fixture
def departure(session_app, monkeypatch):
    from app.services.car_heater.car_heater_service import CarHeaterService
    from app.services.car_heater.departure_service import DepartureService

    monkeypatch.setattr(
        "redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    clock = [datetime(2027, 1, 15, 12, tzinfo=UTC).timestamp()]
    observation = SimpleNamespace(value=-10, time=datetime.fromtimestamp(clock[0], UTC).isoformat())
    weather = SimpleNamespace(
        get_latest=lambda: SimpleNamespace(
            t2m=observation,
            ws_10min=None,
            rh=None,
            n_man=None,
            fmisid="101486",
            station_name="Test station",
            lat=None,
            lon=None,
        )
    )
    weather.get_cached = weather.get_latest
    heater = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    heater.stop()
    planner = DepartureService(session_app.ctrl, heater, weather, clock=lambda: clock[0])
    session_app.car_heater_service = heater
    session_app.departure_service = planner
    heater.bind_departure_planner(planner)
    yield planner, heater, clock, observation


def test_command_delivery_runs_the_saved_plan_without_a_cabin_model(departure, client, session_app):
    _planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    heater.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "power_w": 0,
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )
    commands = heater.get_queued_commands()
    assert len(commands) == 1
    assert commands[0]["action"] == "turn_on"
    assert commands[0]["finish_at"] == datetime.fromisoformat(plan["finish_at"]).timestamp()
    assert client.get("/api/car_heater/departure").get_json()["status"] == "running"


@pytest.mark.parametrize(
    "temperature, minutes",
    [(5, 90), (0, 90), (-2.5, 105), (-5, 120), (-10, 150), (-15, 180), (-30, 180), (-0.1, 91)],
)
def test_preview_uses_duration_table_without_saving(
    departure, session_app, client, temperature, minutes
):
    _planner, heater, _clock, weather = departure
    weather.value = temperature
    response = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    )
    assert response.status_code == 200
    preview = response.get_json()
    assert preview["preheat_minutes"] == minutes
    assert preview["grace_minutes"] == min(30, 180 - minutes)
    assert preview["fallback_reason"] is None
    assert preview["departure_at"] == "2027-01-16T06:00:00+00:00"
    assert session_app.ctrl.list_car_heater_sessions() == []
    assert heater.get_queued_commands() == []


def test_late_start_uses_saved_identity_and_finish_and_retains_weather(
    departure, session_app, client
):
    planner, heater, clock, weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    start = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    finish = datetime.fromisoformat(plan["finish_at"]).timestamp()
    clock[0] = start + 600
    planner.tick()
    assert planner.get_plan()["status"] == "waiting_for_device"
    assert heater.get_queued_commands() == []
    weather.time = datetime.fromtimestamp(clock[0], UTC).isoformat()
    heater.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "power_w": 0,
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )
    planner.tick()
    command = heater.get_queued_commands()[0]
    assert command["session_id"] == plan["session_id"]
    assert command["finish_at"] == finish
    assert command["issued_at"] == clock[0]
    assert planner.get_plan()["late_start_seconds"] == 600
    assert len(session_app.ctrl.list_car_heater_sessions()) == 1
    assert (
        client.post(
            "/api/car_heater/departure",
            json={"departure_at": "2027-01-17T08:00"},
            headers=headers(session_app, client),
        ).status_code
        == 409
    )
    clock[0] = datetime.fromisoformat(plan["departure_at"]).timestamp()
    planner.tick()
    snapshots = client.get("/api/car_heater/sessions/" + plan["session_id"]).get_json()[
        "departure_plan"
    ]["weather_snapshots"]
    assert set(snapshots) == {"planning", "start", "departure"}
    clock[0] = finish
    assert [c["action"] for c in heater.get_queued_commands()] == ["turn_off"]


def test_saved_revisions_reload_and_cancel_without_controlling_heating(
    departure, session_app, client
):
    _planner, heater, clock, _ = departure
    auth = headers(session_app, client)
    endpoint = "/api/car_heater/departure"
    assert session_app.test_client().get(endpoint).status_code == 401
    assert client.post(endpoint, json={}).status_code == 400
    first = client.post(endpoint, json={"departure_at": "2027-01-16T08:00"}, headers=auth)
    assert first.status_code == 201
    first = first.get_json()
    second = client.post(
        endpoint, json={"departure_at": "2027-01-17T08:00"}, headers=auth
    ).get_json()
    assert second["supersedes"] == first["session_id"]
    assert second["session_id"] != first["session_id"]
    detail = client.get("/api/car_heater/sessions/" + first["session_id"]).get_json()
    assert detail["departure_plan"]["status"] == "superseded"
    assert detail["requested_departure_at"] == "2027-01-16T06:00:00+00:00"
    from app.services.car_heater.departure_service import DepartureService

    session_app.departure_service = DepartureService(
        session_app.ctrl, heater, clock=lambda: clock[0]
    )
    assert client.get(endpoint).get_json()["session_id"] == second["session_id"]
    assert client.delete(endpoint, headers=auth).get_json()["status"] == "cancelled"
    assert len(client.get("/api/car_heater/sessions").get_json()) == 2
    assert heater.get_queued_commands() == []


@pytest.mark.parametrize("age, expected", [(0, 150), (1800, 150), (1800.001, 180), (-1, 180)])
def test_observation_time_controls_freshness_not_retrieval(
    departure, session_app, client, age, expected
):
    _planner, _heater, clock, weather = departure
    weather.time = datetime.fromtimestamp(clock[0] - age, UTC).isoformat()
    preview = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    assert preview["preheat_minutes"] == expected
    assert bool(preview["fallback_reason"]) is (expected == 180)
    assert preview["weather"]["observed_at"] == weather.time
    assert preview["grace_minutes"] == (0 if expected == 180 else 30)


@pytest.mark.parametrize("value", [None, True, "-10", float("nan"), float("inf"), -81, 61])
def test_invalid_weather_falls_back_without_extra_grace(departure, session_app, client, value):
    _planner, _heater, _clock, weather = departure
    weather.value = value
    result = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    assert result["preheat_minutes"] == 180
    assert result["grace_minutes"] == 0
    assert result["finish_at"] == result["departure_at"]
    assert result["fallback_reason"]


@pytest.mark.parametrize("stamp", [None, "invalid", "2027-01-15T12:00:00"])
def test_invalid_observation_timestamp_is_a_visible_fallback(departure, session_app, client, stamp):
    _planner, _heater, _clock, weather = departure
    weather.time = stamp
    result = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    assert result["preheat_minutes"] == 180
    assert result["fallback_reason"]


@pytest.mark.parametrize(
    "wall_time, fold, expected",
    [
        ("2027-03-28T03:30", None, None),
        ("2027-10-31T03:30", None, None),
        ("2027-10-31T03:30", 0, "2027-10-31T00:30:00+00:00"),
        ("2027-10-31T03:30", 1, "2027-10-31T01:30:00+00:00"),
        ("2027-03-28T04:30", None, "2027-03-28T01:30:00+00:00"),
        ("2027-01-16T00:15", None, "2027-01-15T22:15:00+00:00"),
    ],
)
def test_helsinki_dst_and_midnight_are_explicit(
    departure, session_app, client, wall_time, fold, expected
):
    response = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": wall_time, "fold": fold},
        headers=headers(session_app, client),
    )
    assert response.status_code == (200 if expected else 400)
    if expected:
        plan = response.get_json()
        assert plan["departure_at"] == expected
        assert (
            datetime.fromisoformat(plan["finish_at"])
            - datetime.fromisoformat(plan["planned_start_at"])
        ).total_seconds() == 10800
    else:
        assert "Europe/Helsinki" in response.get_json()["error"]


def test_weather_change_requires_a_new_preview_before_explicit_save(departure, session_app, client):
    _planner, _heater, _clock, weather = departure
    auth = headers(session_app, client)
    body = {"departure_at": "2027-01-16T08:00"}
    preview = client.post("/api/car_heater/departure/preview", json=body, headers=auth).get_json()
    weather.value = -15
    result = client.post(
        "/api/car_heater/departure", json={**body, "expected_preview": preview}, headers=auth
    )
    assert result.status_code == 409
    assert "preview" in result.get_json()["error"].lower()
    assert client.get("/api/car_heater/departure").get_json() is None


def test_missing_weather_service_and_mutation_guards(departure, session_app, client):
    from app.services.car_heater.departure_service import DepartureService

    _planner, heater, clock, _weather = departure
    session_app.departure_service = DepartureService(
        session_app.ctrl, heater, clock=lambda: clock[0]
    )
    endpoint = "/api/car_heater/departure"
    for method, suffix in [("POST", ""), ("POST", "/preview"), ("DELETE", "")]:
        assert client.open(endpoint + suffix, method=method, json={}).status_code == 400
    auth = headers(session_app, client)
    result = client.post(
        endpoint, json={"departure_at": "2027-01-16T08:00"}, headers=auth
    ).get_json()
    assert result["preheat_minutes"] == 180
    assert result["grace_minutes"] == 0
    assert result["weather"]["observed_at"] is None
    assert result["fallback_reason"] == "Weather unavailable"


@pytest.mark.parametrize(
    "grace, expected",
    [(0, 0), (15, 15), (30, 30), (31, None), (-1, None), (True, None), (1.5, None)],
)
def test_requested_grace_is_bounded(departure, session_app, client, grace, expected):
    response = client.post(
        "/api/car_heater/departure/preview",
        json={"departure_at": "2027-01-16T08:00", "grace_minutes": grace},
        headers=headers(session_app, client),
    )
    assert response.status_code == (400 if expected is None else 200)
    if expected is not None:
        assert response.get_json()["grace_minutes"] == expected


@pytest.mark.parametrize("transport", ["http", "websocket"])
def test_real_telemetry_starts_and_finishes_departure_without_temperature_handoff(
    departure, session_app, client, transport
):
    from test_car_heater_sensors import _DummySIOHandler, _DummySocketIO

    from app.services.esp32_redis_bridge import ESP32RedisBridge

    planner, heater, clock, weather = departure
    session_app.sio_handler = _DummySIOHandler()
    _key, token = session_app.ctrl.create_api_key("isolated-departure")
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp() + 600
    weather.time = datetime.fromtimestamp(clock[0], UTC).isoformat()
    frame = {
        "timestamp": datetime.fromtimestamp(clock[0], UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": 40,
        "shelly_connected": False,
        "firmware_version": "3.1.1",
        "bounded": {
            "protocol": 1,
            "ready": True,
            "relay_on": False,
            "power_w": 0,
            "boot_id": "relay",
            "device_boot_id": "esp",
        },
    }

    def deliver(payload):
        if transport == "http":
            response = client.post(
                "/api/car_heater/status", json=payload, headers={"X-API-Key": token}
            )
            assert response.status_code == 200
            return response.get_json()
        bridge = ESP32RedisBridge(
            app=session_app, socketio=_DummySocketIO(), sio_handler=session_app.sio_handler
        )
        bridge._emit_status(payload)
        return heater.get_queued_commands()

    commands = deliver(frame)
    assert [c["action"] for c in commands] == ["turn_on"]
    assert commands[0]["session_id"] == plan["session_id"]
    assert commands[0]["finish_at"] == datetime.fromisoformat(plan["finish_at"]).timestamp()
    frame["bounded"].update(
        session_id=plan["session_id"], relay_on=True, power_w=1700, phase="heating"
    )
    assert deliver(frame) == []
    assert heater.get_bounded_session()["phase"] == "heating"
    assert heater.get_bounded_session()["configuration"]["firmware_version"] == "3.1.1"
    clock[0] = datetime.fromisoformat(plan["departure_at"]).timestamp()
    weather.value = -5
    weather.time = datetime.fromtimestamp(clock[0], UTC).isoformat()
    deliver(frame)
    detail = client.get("/api/car_heater/sessions/" + plan["session_id"]).get_json()
    assert (
        detail["departure_plan"]["weather_snapshots"]["start"]["observed_at"]
        != detail["departure_plan"]["weather_snapshots"]["planning"]["observed_at"]
    )
    assert detail["departure_plan"]["weather_snapshots"]["departure"]["temperature_c"] == -5
    assert any(m["kind"] == "powered_on" for m in detail["execution"]["milestones"])
    clock[0] = commands[0]["finish_at"]
    off = deliver(frame)
    assert [c["action"] for c in off] == ["turn_off"]
    frame["bounded"].update(relay_on=False, power_w=0, phase="stopped", reason="local_expiry")
    deliver(frame)
    planner.tick()
    assert client.get("/api/car_heater/departure").get_json()["status"] == "completed"
    assert heater.get_bounded_session()["off_state"] == "confirmed"
    assert heater.get_queued_commands() == []


def test_restart_after_execution_attempt_requests_off_without_resuming_or_extending(
    departure, session_app, client
):
    from app.services.car_heater.car_heater_service import CarHeaterService
    from app.services.car_heater.departure_service import DepartureService

    _planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    heater.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )
    heater.get_queued_commands()
    recovered = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    recovered.stop()
    session_app.car_heater_service = recovered
    planner = DepartureService(session_app.ctrl, recovered, clock=lambda: clock[0])
    session_app.departure_service = planner
    recovered.bind_departure_planner(planner)
    assert client.get("/api/car_heater/departure").get_json()["status"] == "interrupted"
    assert [c["action"] for c in recovered.get_queued_commands()] == ["turn_off"]
    assert (
        recovered.get_bounded_session()["finish_at"]
        == datetime.fromisoformat(plan["finish_at"]).timestamp()
    )
    assert (
        client.post(
            "/api/car_heater/departure",
            json={"departure_at": "2027-01-17T08:00"},
            headers=headers(session_app, client),
        ).status_code
        == 409
    )


def test_no_start_after_original_finish(departure, session_app, client):
    planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["finish_at"]).timestamp()
    assert heater.get_queued_commands() == []
    assert planner.get_plan()["status"] == "expired"
    assert "departure" in planner.get_plan()["weather_snapshots"]
    assert session_app.ctrl.get_car_heater_execution(plan["session_id"]) is None


def test_cancelling_after_start_requests_off_and_blocks_replacement_until_confirmed(
    departure, session_app, client
):
    planner, heater, clock, _weather = departure
    auth = headers(session_app, client)
    plan = client.post(
        "/api/car_heater/departure", json={"departure_at": "2027-01-16T08:00"}, headers=auth
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    heater.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )
    planner.tick()
    assert (
        client.delete("/api/car_heater/departure", headers=auth).get_json()["status"] == "stopping"
    )
    commands = heater.get_queued_commands()
    assert [c["action"] for c in commands] == ["turn_off"]
    assert commands[0]["finish_at"] == datetime.fromisoformat(plan["finish_at"]).timestamp()
    assert (
        client.post(
            "/api/car_heater/departure", json={"departure_at": "2027-01-17T08:00"}, headers=auth
        ).status_code
        == 409
    )
    heater.observe_bounded_status(
        {"bounded": {"session_id": plan["session_id"], "relay_on": False, "phase": "stopped"}}
    )
    planner.tick()
    assert client.get("/api/car_heater/departure").get_json()["status"] == "cancelled"
    assert heater.get_queued_commands() == []


def test_background_scheduler_runs_without_a_browser_or_telemetry_tick(
    departure, session_app, client
):
    import time

    from app.services.car_heater.car_heater_service import CarHeaterService
    from app.services.car_heater.departure_service import DepartureService

    _planner, _heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    heater = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
    try:
        heater.observe_bounded_status(
            {
                "bounded": {
                    "protocol": 1,
                    "ready": True,
                    "relay_on": False,
                    "boot_id": "relay",
                    "device_boot_id": "esp",
                }
            }
        )
        heater.bind_departure_planner(
            DepartureService(session_app.ctrl, heater, clock=lambda: clock[0])
        )
        deadline = time.monotonic() + 4
        while heater.get_bounded_session() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        run = heater.get_bounded_session()
        assert run is not None
        assert run["session_id"] == plan["session_id"]
        assert run["finish_at"] == datetime.fromisoformat(plan["finish_at"]).timestamp()
    finally:
        heater.stop()


def test_delayed_execution_expires_without_rearming_the_saved_plan(departure, session_app, client):
    planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    heater.observe_bounded_status(
        {
            "bounded": {
                "protocol": 1,
                "ready": True,
                "relay_on": False,
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )
    command = heater.get_queued_commands()[0]
    clock[0] += 31
    assert command["expires_at"] < clock[0]
    off = heater.get_queued_commands()
    assert [c["action"] for c in off] == ["turn_off"]
    assert off[0]["finish_at"] == command["finish_at"]
    heater.observe_bounded_status(
        {"bounded": {"session_id": plan["session_id"], "relay_on": False, "phase": "stopped"}}
    )
    planner.tick()
    assert heater.get_queued_commands() == []
    assert planner.get_plan()["status"] != "scheduled"


@pytest.mark.parametrize("next_action", ["restart", "manual"])
def test_departure_reconciles_its_own_durable_stopped_execution(
    departure, session_app, client, next_action
):
    from app.services.car_heater.car_heater_service import CarHeaterService
    from app.services.car_heater.departure_service import DepartureService

    planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(session_app, client),
    ).get_json()
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    ready = {
        "protocol": 1,
        "ready": True,
        "relay_on": False,
        "boot_id": "relay",
        "device_boot_id": "esp",
    }
    heater.observe_bounded_status({"bounded": ready})
    heater.get_queued_commands()
    client.delete("/api/car_heater/bounded", headers=headers(session_app, client))
    heater.observe_bounded_status(
        {"bounded": {**ready, "session_id": plan["session_id"], "phase": "stopped"}}
    )
    if next_action == "restart":
        heater = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
        heater.stop()
        planner = DepartureService(session_app.ctrl, heater, clock=lambda: clock[0])
    else:
        heater.start_bounded_session(60)
    session_app.departure_service = planner
    planner.tick()
    result = client.get("/api/car_heater/departure").get_json()
    assert result["status"] == "cancelled"
    assert result["events"][-1]["reason"] == "cancelled"
