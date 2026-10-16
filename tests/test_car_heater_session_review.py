"""Session measurement review through public telemetry and session endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import test_car_heater_sessions as evidence
from test_car_heater_photos import upload
from test_car_heater_sensors import _DummySIOHandler, _DummySocketIO

from app.blueprints.api.car_heater import control as car_control, status as car_status
from app.services.esp32_redis_bridge import ESP32RedisBridge

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers

_NOW = [datetime.fromisoformat("2026-10-02T03:00:00+00:00")]


@pytest.fixture
def review_app(session_app, monkeypatch):
    session_app.sio_handler = _DummySIOHandler()
    monkeypatch.setattr(car_control, "process_commands", lambda data, car: ([], None, None))
    monkeypatch.setattr(car_control, "sync_runtime_command_state", lambda data, car: (None, None))
    monkeypatch.setattr(car_status, "_utc_now", lambda: _NOW[0])
    return session_app


def ok(temperature_c: float, **extra: Any) -> dict[str, Any]:
    return {"status": "ok", "temperature_c": temperature_c, "age_ms": 0, **extra}


def post_frame(
    app,
    at: str,
    *,
    reference=None,
    comparison=None,
    cabin=None,
    shelly: dict | None = None,
    transport: str = "http",
    **extra: Any,
) -> None:
    """Send one simulated device frame sampled at ``at`` (UTC) and received 1 s later."""
    sampled = datetime.fromisoformat(at)
    _NOW[0] = sampled + timedelta(seconds=1)
    sensors = {
        key: value
        for key, value in (
            ("glass_reference", reference),
            ("glass_comparison", comparison),
            ("cabin", cabin),
        )
        if value is not None
    }
    frame: dict[str, Any] = {
        "timestamp": sampled.strftime("%Y-%m-%d %H:%M:%S"),
        "shelly_connected": shelly is not None,
        "temperature": None,
        "firmware_version": "3.1.0",
        "sensors": sensors,
        **extra,
    }
    if shelly is not None:
        frame["shelly"] = json.dumps(shelly)
    with app.app_context():
        if transport == "ws":
            frame["received_at"] = _NOW[0].isoformat()
            bridge = ESP32RedisBridge(
                app=app, socketio=_DummySocketIO(), sio_handler=app.sio_handler
            )
            bridge._emit_status(json.loads(json.dumps(frame)))
        else:
            _, code = car_status.handle_status_update_request(frame, commands_enabled=True)
            assert code == 200


def create_session(app, client, **body) -> str:
    response = client.post(
        "/api/car_heater/sessions",
        json={"trigger": "manual", **body},
        headers=headers(app, client),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def review(client, session_id: str) -> dict[str, Any]:
    response = client.get(f"/api/car_heater/sessions/{session_id}/measurements")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_review_returns_the_sessions_glass_and_cabin_trajectories(review_app, client):
    session_id = create_session(
        review_app,
        client,
        observed_at="2026-10-02T05:00:00+02:00",
        heating_duration_minutes=60,
        installation="Reference lower-middle; comparison lower driver band",
    )
    post_frame(review_app, "2026-10-02T01:00:00+00:00", reference=ok(-9.0))  # previous night
    post_frame(
        review_app,
        "2026-10-02T03:05:00+00:00",
        reference=ok(-4.5),
        comparison=ok(-5.25),
        cabin=ok(-2.0, humidity_pct=81.5),
    )
    post_frame(review_app, "2026-10-02T06:00:00+00:00", reference=ok(9.0))  # later in the day

    data = review(client, session_id)

    assert data["window"]["start"] == "2026-10-02T02:30:00+00:00"
    assert data["window"]["end"] == "2026-10-02T04:15:00+00:00"
    assert data["series"]["glass_reference"] == [
        {"t": "2026-10-02T03:05:00.000+00:00", "temperature_c": -4.5, "state": "ok"}
    ]
    assert data["series"]["glass_comparison"] == [
        {"t": "2026-10-02T03:05:00.000+00:00", "temperature_c": -5.25, "state": "ok"}
    ]
    assert data["series"]["cabin"] == [
        {
            "t": "2026-10-02T03:05:00.000+00:00",
            "temperature_c": -2.0,
            "humidity_pct": 81.5,
            "state": "ok",
        }
    ]
    assert data["configuration"]["snapshot"]["installation"] == (
        "Reference lower-middle; comparison lower driver band"
    )
    assert data["configuration"]["reported_firmware_versions"] == ["3.1.0"]


@pytest.fixture
def departure(review_app, monkeypatch):
    from test_departure_heating import departure as departure_fixture

    fixture = departure_fixture.__wrapped__(review_app, monkeypatch)
    yield next(fixture)
    fixture.close()


def start_departure(app, client, departure) -> dict[str, Any]:
    """Save a 08:00 departure at -10 °C (150 min preheat) and deliver its ON."""
    _planner, heater, clock, _weather = departure
    plan = client.post(
        "/api/car_heater/departure",
        json={"departure_at": "2027-01-16T08:00"},
        headers=headers(app, client),
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
    assert heater.get_queued_commands()[0]["action"] == "turn_on"
    return plan


def shelly(power_w: float, energy_wh: float, *, on: bool = True) -> dict[str, Any]:
    return {"output": on, "apower": power_w, "aenergy": {"total": energy_wh}}


def test_departure_review_keeps_power_gaps_when_shelly_is_unavailable(
    review_app, client, departure
):
    plan = start_departure(review_app, client, departure)
    assert plan["planned_start_at"] == "2027-01-16T03:30:00+00:00"
    post_frame(
        review_app, "2027-01-16T03:31:00+00:00", reference=ok(-8.0), shelly=shelly(1650, 1000)
    )
    post_frame(review_app, "2027-01-16T03:32:00+00:00", reference=ok(-7.5))  # Shelly missing
    post_frame(
        review_app,
        "2027-01-16T03:33:00+00:00",
        reference=ok(-7.0),
        shelly=shelly(1640.5, 1001.5),
    )

    data = review(client, plan["session_id"])

    assert data["window"]["anchor_source"] == "execution"
    assert data["window"]["heating_anchor"] == "2027-01-16T03:30:00+00:00"
    assert data["window"]["start"] == "2027-01-16T03:00:00+00:00"
    assert data["window"]["end"] == "2027-01-16T06:45:00+00:00"
    assert [p["temperature_c"] for p in data["series"]["glass_reference"]] == [-8.0, -7.5, -7.0]
    assert data["series"]["power"] == [
        {
            "t": "2027-01-16T03:31:00+00:00",
            "power_w": 1650,
            "relay_on": True,
            "energy_total_wh": 1000,
        },
        {
            "t": "2027-01-16T03:33:00+00:00",
            "power_w": 1640.5,
            "relay_on": True,
            "energy_total_wh": 1001.5,
        },
    ]
    assert data["energy"] == {
        "first_wh": 1000,
        "last_wh": 1001.5,
        "used_wh": 1.5,
        "source": "shelly_counter",
    }


def reference_samples(app, start: str, values: list[float | None], step_s: int = 30) -> None:
    """Reference-glass samples every ``step_s``; ``None`` skips a report entirely."""
    first = datetime.fromisoformat(start)
    for index, value in enumerate(values):
        if value is not None:
            at = (first + timedelta(seconds=index * step_s)).isoformat()
            post_frame(app, at, reference=ok(value))


def indicator(client, session_id: str) -> dict[str, Any]:
    return review(client, session_id)["glass_indicator"]


@pytest.fixture
def manual_session(review_app, client) -> str:
    return create_session(
        review_app, client, observed_at="2026-10-02T05:00:00+02:00", heating_duration_minutes=90
    )


def test_experimental_indicator_needs_ten_minutes_at_or_above_three_degrees(
    review_app, client, manual_session
):
    # 03:10:00 is 2.9 °C; 3.0 °C holds from 03:10:30 for 21 samples (10 min).
    reference_samples(review_app, "2026-10-02T03:10:00+00:00", [2.9] + [3.0] * 21)

    result = indicator(client, manual_session)

    assert result["experimental"] is True
    assert result["controls_heating"] is False
    assert result["sensor"] == "glass_reference"
    assert (result["threshold_c"], result["dwell_s"]) == (3.0, 600)
    assert result["status"] == "reached"
    assert result["first_reached_at"] == "2026-10-02T03:20:30.000+00:00"
    assert result["runs"] == [
        {
            "started_at": "2026-10-02T03:10:30.000+00:00",
            "last_valid_at": "2026-10-02T03:20:30.000+00:00",
            "reached_at": "2026-10-02T03:20:30.000+00:00",
            "ended_at": None,
            "end_reason": "window_end",
        }
    ]


def test_gap_longer_than_sixty_seconds_restarts_the_dwell(
    review_app, client, manual_session, monkeypatch
):
    # A fixed definition: changing the live freshness setting cannot move candidate times.
    monkeypatch.setenv("CAR_HEATER_SENSOR_MAX_AGE_S", "300")
    # 3.5 °C from 03:10 to 03:16, no report for 90 s, then until 03:24.
    reference_samples(
        review_app, "2026-10-02T03:10:00+00:00", [3.5] * 13 + [None, None] + [3.5] * 17
    )

    result = indicator(client, manual_session)

    assert (result["max_gap_s"], result["definition_version"]) == (60, 1)
    assert result["status"] == "not_reached"
    assert [(r["started_at"], r["end_reason"], r["ended_at"]) for r in result["runs"]] == [
        ("2026-10-02T03:10:00.000+00:00", "gap", "2026-10-02T03:17:30.000+00:00"),
        ("2026-10-02T03:17:30.000+00:00", "window_end", None),
    ]


@pytest.mark.parametrize(
    "failed, reason",
    [
        ({"status": "missing", "temperature_c": None, "age_ms": None}, "missing"),
        ({"status": "ok", "temperature_c": 250.0, "age_ms": 0}, "invalid"),
        ({"status": "cached", "temperature_c": 3.5, "age_ms": 2000, "error": "read"}, "cached"),
    ],
)
def test_failed_reference_read_ends_the_dwell(review_app, client, manual_session, failed, reason):
    reference_samples(review_app, "2026-10-02T03:10:00+00:00", [3.5] * 11)
    post_frame(review_app, "2026-10-02T03:15:20+00:00", reference=failed)
    reference_samples(review_app, "2026-10-02T03:15:40+00:00", [3.5] * 12)

    result = indicator(client, manual_session)

    assert result["status"] == "not_reached"
    assert result["runs"][0]["end_reason"] == reason
    assert result["runs"][0]["ended_at"] == "2026-10-02T03:15:21.000+00:00"
    assert result["runs"][1]["started_at"] == "2026-10-02T03:15:40.000+00:00"


def test_late_sample_counts_as_trajectory_but_not_as_dwell_evidence(
    review_app, client, manual_session
):
    reference_samples(review_app, "2026-10-02T03:10:00+00:00", [3.5] * 11)
    late = datetime.fromisoformat("2026-10-02T03:17:00+00:00")
    _NOW[0] = late
    with review_app.app_context():
        car_status.handle_status_update_request(
            {
                "timestamp": late.strftime("%Y-%m-%d %H:%M:%S"),
                "shelly_connected": False,
                "temperature": None,
                "sensors": {"glass_reference": ok(3.6, age_ms=90_000)},  # sampled 03:15:30
            }
        )
    reference_samples(review_app, "2026-10-02T03:17:10+00:00", [3.5] * 5)

    data = review(client, manual_session)

    assert {"t": "2026-10-02T03:15:30.000+00:00", "temperature_c": 3.6, "state": "stale"} in (
        data["series"]["glass_reference"]
    )
    assert data["glass_indicator"]["status"] == "not_reached"
    assert data["glass_indicator"]["runs"][0]["end_reason"] == "stale"


def test_downward_crossing_ends_a_reached_run_and_keeps_the_first_candidate_time(
    review_app, client, manual_session
):
    reference_samples(review_app, "2026-10-02T03:10:00+00:00", [3.2] * 21 + [2.5, 2.5] + [3.1] * 3)

    result = indicator(client, manual_session)

    assert result["status"] == "reached"
    assert result["first_reached_at"] == "2026-10-02T03:20:00.000+00:00"
    assert [(r["end_reason"], r["ended_at"], r["reached_at"]) for r in result["runs"]] == [
        ("fell_below", "2026-10-02T03:20:30.000+00:00", "2026-10-02T03:20:00.000+00:00"),
        ("window_end", None, None),
    ]


def test_only_the_reference_glass_drives_the_indicator(review_app, client, manual_session):
    first = datetime.fromisoformat("2026-10-02T03:10:00+00:00")
    for index in range(25):
        at = (first + timedelta(seconds=30 * index)).isoformat()
        post_frame(
            review_app,
            at,
            reference=ok(1.0),
            comparison=ok(6.0),
            cabin=ok(15.0, humidity_pct=40.0),
        )

    result = indicator(client, manual_session)

    assert result["status"] == "not_reached"
    assert result["runs"] == []
    assert result["valid_samples"] == 25


def test_no_reference_samples_is_reported_as_no_evidence(review_app, client, manual_session):
    post_frame(review_app, "2026-10-02T03:10:00+00:00", cabin=ok(4.0, humidity_pct=90.0))

    data = review(client, manual_session)

    assert data["glass_indicator"]["status"] == "no_valid_evidence"
    assert data["series"]["glass_reference"] == []
    assert data["series"]["power"] == []
    assert data["energy"]["used_wh"] is None


def save_outcome(app, client, session_id, **outcome) -> None:
    response = client.put(
        f"/api/car_heater/sessions/{session_id}/outcome",
        json={"result": "clear", **outcome},
        headers=headers(app, client),
    )
    assert response.status_code == 200, response.get_json()


def test_already_clear_first_photo_is_only_an_upper_bound(review_app, client, manual_session):
    auth = headers(review_app, client)
    for captured in ("2026-10-02T03:50:00.000Z", "2026-10-02T03:40:00.000Z"):
        assert (
            upload(
                client,
                auth,
                manual_session,
                captured_at=captured,
                capture_kind="manual",
                due_at=None,
                sequence=None,
            ).status_code
            == 201
        )
    save_outcome(review_app, client, manual_session, already_clear_at_observation=True)

    clearing = review(client, manual_session)["clearing"]

    assert clearing == {
        "interpretation": "upper_bound",
        "clear_no_later_than": "2026-10-02T03:40:00+00:00",
        "basis": "first_photo",
        "exact_clearing_time": None,
        "fully_observed_trajectory": False,
    }


def test_already_clear_bound_uses_the_earliest_observation_evidence(
    review_app, client, manual_session
):
    auth = headers(review_app, client)
    observation = client.put(
        f"/api/car_heater/sessions/{manual_session}/observations/{uuid4()}",
        json={"started_at": "2026-10-02T03:38:00Z", "finished_at": None, "missed": []},
        headers=auth,
    )
    assert observation.status_code in (200, 201), observation.get_json()
    upload(
        client,
        auth,
        manual_session,
        captured_at="2026-10-02T03:40:00.000Z",
        capture_kind="manual",
        due_at=None,
        sequence=None,
    )
    save_outcome(review_app, client, manual_session, already_clear_at_observation=True)

    clearing = review(client, manual_session)["clearing"]

    assert (clearing["clear_no_later_than"], clearing["basis"]) == (
        "2026-10-02T03:38:00+00:00",
        "observation_start",
    )


@pytest.mark.parametrize(
    "outcome, interpretation",
    [(None, "outcome_not_recorded"), ({"result": "partial_scrape"}, "clearing_not_observed")],
)
def test_review_never_invents_a_clearing_time(
    review_app, client, manual_session, outcome, interpretation
):
    if outcome:
        save_outcome(review_app, client, manual_session, **outcome)

    clearing = review(client, manual_session)["clearing"]

    assert clearing["interpretation"] == interpretation
    assert clearing["clear_no_later_than"] is None
    assert clearing["exact_clearing_time"] is None
    assert clearing["fully_observed_trajectory"] is False


@pytest.mark.parametrize("transport", ["http", "ws"])
def test_partial_and_failed_frames_leave_honest_gaps_after_app_reload(
    review_app, client, manual_session, transport
):
    from app.core import Controller

    post_frame(
        review_app,
        "2026-10-02T03:10:00+00:00",
        reference=ok(-1.0),
        comparison=ok(-2.0),
        cabin=ok(4.0, humidity_pct=88.0),
        shelly=shelly(1650, 1000),
        transport=transport,
    )
    # Partial frame: comparison/cabin omitted, so their trajectories are untouched.
    post_frame(review_app, "2026-10-02T03:10:10+00:00", reference=ok(-0.75), transport=transport)
    post_frame(
        review_app,
        "2026-10-02T03:10:20+00:00",
        reference=ok(-0.5),
        comparison={"status": "missing", "temperature_c": None, "age_ms": None},
        cabin=ok(4.5, humidity_pct=87.0),
        shelly=shelly(0, 1000.25, on=False),
        transport=transport,
    )
    review_app.ctrl = Controller(db_path=review_app.config["DB_PATH"])
    review_app.ctrl._sa_engine = review_app.extensions["session_engine"]
    review_app.extensions.pop("car_heater_sensor_tracker", None)

    data = review(client, manual_session)

    assert data["gap_after_s"] == 60.0
    assert [p["temperature_c"] for p in data["series"]["glass_reference"]] == [-1.0, -0.75, -0.5]
    assert data["series"]["glass_comparison"] == [
        {"t": "2026-10-02T03:10:00.000+00:00", "temperature_c": -2.0, "state": "ok"},
        {"t": "2026-10-02T03:10:21.000+00:00", "temperature_c": None, "state": "missing"},
    ]
    assert [(p["temperature_c"], p["humidity_pct"]) for p in data["series"]["cabin"]] == [
        (4.0, 88.0),
        (4.5, 87.0),
    ]
    assert [(p["t"], p["power_w"], p["relay_on"]) for p in data["series"]["power"]] == [
        ("2026-10-02T03:10:00+00:00", 1650, True),
        ("2026-10-02T03:10:20+00:00", 0, False),  # confirmed zero, not a gap
    ]


def test_review_requires_sign_in_and_an_existing_session(review_app, client, manual_session):
    anonymous = review_app.test_client()
    assert anonymous.get(f"/api/car_heater/sessions/{manual_session}/measurements").status_code in (
        401,
        302,
    )
    assert client.get("/api/car_heater/sessions/unknown/measurements").status_code == 404


def test_reached_indicator_neither_stops_heating_nor_hands_off_to_comfort(
    review_app, client, departure
):
    _planner, heater, clock, _weather = departure
    plan = start_departure(review_app, client, departure)
    relay_on = {
        "bounded": {
            "protocol": 1,
            "ready": True,
            "relay_on": True,
            "power_w": 1650,
            "session_id": plan["session_id"],
            "boot_id": "relay",
            "device_boot_id": "esp",
        }
    }
    heater.observe_bounded_status(relay_on)
    before = heater.get_bounded_session()
    assert before["phase"] == "heating"
    first = datetime.fromisoformat("2027-01-16T03:31:00+00:00")
    for index in range(25):
        at = first + timedelta(seconds=30 * index)
        clock[0] = at.timestamp()
        post_frame(
            review_app,
            at.isoformat(),
            reference=ok(4.0),
            cabin=ok(18.0, humidity_pct=40.0),
            shelly=shelly(1650, 1000 + index),
            **relay_on,
        )
        heater.tick_bounded()

    assert indicator(client, plan["session_id"])["status"] == "reached"
    after = heater.get_bounded_session()
    assert after["phase"] == before["phase"]
    assert after["finish_at"] == before["finish_at"]
    assert [m["kind"] for m in after["milestones"]] == [m["kind"] for m in before["milestones"]]
    assert all(c["action"] != "turn_off" for c in heater.get_queued_commands())
    assert client.get("/api/car_heater/departure").get_json()["status"] == "running"
