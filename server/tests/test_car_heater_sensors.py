"""Glass and cabin sensor telemetry through the HTTP and WebSocket status paths."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from flask import Flask
from sqlalchemy import create_engine

from app.blueprints.api.car_heater import control as car_control, status as car_status
from app.core import Controller
from app.core.schema import metadata
from app.services.esp32_redis_bridge import ESP32RedisBridge

FRAME_TS = "2026-11-02 05:10:00"  # device frames carry UTC wall time
DAY_START = "2026-11-02T00:00:00+00:00"
DAY_END = "2026-11-02T23:59:59+00:00"


class _DummySIOHandler:
    def __init__(self) -> None:
        self.status_events: list[dict[str, Any]] = []

    def emit_to_views(self, event: str, payload: dict[str, Any]) -> None:
        pass

    def emit_car_heater_status_to_views(
        self,
        status_payload: dict[str, Any],
        command_status: dict[str, Any] | None,
        charge_mode_state: dict[str, Any] | None,
    ) -> None:
        self.status_events.append(status_payload)

    def emit_ready_by_status_to_views(self) -> None:
        pass

    def emit_kfactor_status(self, _svc: Any) -> None:
        pass


class _DummySocketIO:
    def emit(self, event: str, payload: dict[str, Any]) -> None:
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    metadata.create_all(engine)
    ctrl._sa_engine = engine

    flask_app = Flask(__name__)
    flask_app.ctrl = ctrl
    flask_app.sio_handler = _DummySIOHandler()
    monkeypatch.setattr(car_control, "process_commands", lambda data, car: ([], None, None))
    monkeypatch.setattr(car_control, "sync_runtime_command_state", lambda data, car: (None, None))
    _set_now("2026-11-02T05:10:01+00:00")
    monkeypatch.setattr(car_status, "_utc_now", lambda: _NOW[0])
    yield flask_app
    engine.dispose()


_NOW = [datetime.fromisoformat("2026-11-02T05:10:01+00:00")]


def _set_now(iso: str) -> None:
    _NOW[0] = datetime.fromisoformat(iso)


def _sensors(
    *,
    reference: dict[str, Any] | None = None,
    lower: dict[str, Any] | None = None,
    cabin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sensors: dict[str, Any] = {}
    if reference is not None:
        sensors["glass_reference"] = reference
    if lower is not None:
        sensors["glass_comparison"] = lower
    if cabin is not None:
        sensors["cabin"] = cabin
    return sensors


def _ok(temperature_c: float, age_ms: int = 500, **extra: Any) -> dict[str, Any]:
    return {"status": "ok", "temperature_c": temperature_c, "age_ms": age_ms, **extra}


def _frame(sensors: dict[str, Any], *, timestamp: str = FRAME_TS, **extra: Any) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "shelly_connected": False,
        "temperature": None,
        "firmware_version": "3.0.0",
        "sensors": sensors,
        **extra,
    }


def _post_http(app: Flask, frame: dict[str, Any]) -> dict[str, Any]:
    with app.app_context():
        _, code = car_status.handle_status_update_request(frame, commands_enabled=True)
    assert code == 200
    return app.sio_handler.status_events[-1]


def test_http_frame_without_shelly_persists_glass_and_cabin_readings(app: Flask) -> None:
    frame = _frame(
        _sensors(
            reference=_ok(-1.25),
            lower=_ok(2.5),
            cabin=_ok(21.0, humidity_pct=45.5),
        ),
        temperature=21.0,
    )

    emitted = _post_http(app, frame)

    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert len(rows) == 1
    row = rows[0]
    assert row.glass_reference_temp_c == -1.25
    assert row.glass_comparison_temp_c == 2.5
    assert row.cabin_temp_c == 21.0
    assert row.cabin_humidity_pct == 45.5
    assert row.glass_reference_sampled_at == "2026-11-02T05:09:59.500+00:00"
    assert row.received_at == "2026-11-02T05:10:01.000+00:00"
    assert row.firmware_version == "3.0.0"

    sensors = emitted["sensors"]
    assert sensors["glass_reference"]["temperature_c"] == -1.25
    assert sensors["glass_reference"]["state"] == "fresh"
    assert sensors["cabin"]["humidity_pct"] == 45.5
    assert emitted["shelly_connected"] is False
    assert "is_heater_on" not in emitted  # unknown relay state is not OFF


def _post_ws(app: Flask, frame: dict[str, Any]) -> dict[str, Any]:
    bridge = ESP32RedisBridge(app=app, socketio=_DummySocketIO(), sio_handler=app.sio_handler)
    # The gateway forwards the JSON frame unchanged plus its own receipt time.
    bridge._emit_status(json.loads(json.dumps(frame)))
    return app.sio_handler.status_events[-1]


def _comparable(row: Any) -> dict[str, Any]:
    values = dict(vars(row))
    values.pop("id")
    values.pop("source")
    return values


@pytest.fixture
def second_app(tmp_path, monkeypatch):
    """An independent app/database so both transports start from the same state."""
    ctrl = Controller(db_path=str(tmp_path / "legacy2.db"))
    engine = create_engine(f"sqlite:///{tmp_path / 'test2.db'}")
    metadata.create_all(engine)
    ctrl._sa_engine = engine
    flask_app = Flask(__name__)
    flask_app.ctrl = ctrl
    flask_app.sio_handler = _DummySIOHandler()
    yield flask_app
    engine.dispose()


def test_websocket_and_http_frames_persist_the_same_normalized_measurements(
    app: Flask, second_app: Flask
) -> None:
    sensors = _sensors(
        reference=_ok(-3.5),
        lower={"status": "cached", "temperature_c": -4.0, "age_ms": 20000, "error": "read_failed"},
        cabin={"status": "missing", "temperature_c": None, "humidity_pct": None, "age_ms": None},
    )
    http_emitted = _post_http(app, _frame(sensors))
    ws_emitted = _post_ws(
        second_app, _frame(sensors, received_at="2026-11-02T05:10:01+00:00", device_id="esp")
    )

    http_rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    ws_rows = second_app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert [_comparable(r) for r in ws_rows] == [_comparable(r) for r in http_rows]
    assert http_rows[0].source == "HTTP"
    assert ws_rows[0].source == "WS"
    assert ws_rows[0].glass_comparison_state == "cached"
    assert ws_rows[0].glass_comparison_temp_c is None  # retained values are not new samples
    assert ws_rows[0].cabin_state == "missing"
    assert ws_emitted["sensors"] == http_emitted["sensors"]
    assert ws_emitted["sensors"]["glass_comparison"]["cached"] is True
    assert ws_emitted["sensors"]["cabin"]["state"] == "missing"
    assert ws_emitted["sensors"]["cabin"]["temperature_c"] is None


def test_websocket_frames_use_the_gateway_receipt_time(app: Flask) -> None:
    _post_ws(app, _frame(_sensors(reference=_ok(1.0)), received_at="2026-11-02T05:10:00.800+00:00"))

    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert rows[0].received_at == "2026-11-02T05:10:00.800+00:00"


def test_partial_frame_keeps_sensors_it_omits(app: Flask) -> None:
    _post_http(
        app,
        _frame(_sensors(reference=_ok(-1.0), lower=_ok(-2.0), cabin=_ok(5.0, humidity_pct=80.0))),
    )
    _set_now("2026-11-02T05:10:06+00:00")

    emitted = _post_http(
        app,
        _frame(_sensors(reference=_ok(-0.5)), timestamp="2026-11-02 05:10:05", temperature=None),
    )

    assert emitted["sensors"]["glass_reference"]["temperature_c"] == -0.5
    assert emitted["sensors"]["glass_comparison"]["temperature_c"] == -2.0
    assert emitted["sensors"]["cabin"]["humidity_pct"] == 80.0
    latest = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)[-1]
    assert latest.glass_comparison_state is None  # omitted, not missing
    assert latest.cabin_state is None


def test_true_zero_readings_are_kept_as_zero(app: Flask) -> None:
    emitted = _post_http(
        app, _frame(_sensors(reference=_ok(0.0), cabin=_ok(0.0, humidity_pct=0.0)), temperature=0.0)
    )

    row = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)[0]
    assert row.glass_reference_temp_c == 0.0
    assert row.cabin_humidity_pct == 0.0
    assert emitted["sensors"]["glass_reference"]["temperature_c"] == 0.0
    assert emitted["sensors"]["glass_reference"]["state"] == "fresh"
    assert emitted["ambient_temp"] == 0.0


def test_out_of_order_frame_does_not_replace_newer_reading(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0)), timestamp="2026-11-02 05:10:00"))
    _set_now("2026-11-02T05:10:06+00:00")

    emitted = _post_http(
        app, _frame(_sensors(reference=_ok(-9.0)), timestamp="2026-11-02 05:09:55")
    )

    assert emitted["sensors"]["glass_reference"]["temperature_c"] == -1.0
    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert rows[-1].glass_reference_state == "out_of_order"
    assert rows[-1].glass_reference_temp_c is None


def test_late_sample_is_kept_in_history_but_not_shown_as_current(app: Flask) -> None:
    emitted = _post_http(app, _frame(_sensors(reference=_ok(-1.0, age_ms=120_000))))

    assert "glass_reference" not in emitted.get("sensors", {})
    row = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)[0]
    assert row.glass_reference_state == "stale"
    assert row.glass_reference_temp_c == -1.0  # the trajectory survives a delayed frame
    assert row.glass_reference_sampled_at == "2026-11-02T05:08:00.000+00:00"


def test_resent_late_sample_is_not_stored_twice(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0, age_ms=120_000))))
    _set_now("2026-11-02T05:10:06+00:00")

    _post_http(
        app, _frame(_sensors(reference=_ok(-1.0, age_ms=125_000)), timestamp="2026-11-02 05:10:05")
    )

    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert rows[-1].glass_reference_state == "out_of_order"
    assert rows[-1].glass_reference_temp_c is None


def test_explicit_missing_report_clears_the_current_reading(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0))))
    _set_now("2026-11-02T05:10:06+00:00")

    emitted = _post_http(
        app,
        _frame(
            _sensors(
                reference={
                    "status": "missing",
                    "temperature_c": None,
                    "age_ms": None,
                    "error": "not_found",
                }
            ),
            timestamp="2026-11-02 05:10:05",
        ),
    )

    reference = emitted["sensors"]["glass_reference"]
    assert reference["state"] == "missing"
    assert reference["temperature_c"] is None
    assert reference["error"] == "not_found"
    history = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert history[0].glass_reference_temp_c == -1.0  # stored evidence is untouched


def test_test_mode_frames_do_not_become_live_readings(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0))))
    _set_now("2026-11-02T05:10:06+00:00")

    with app.app_context():
        car_status.handle_status_update_request(
            _frame(_sensors(reference=_ok(25.0)), timestamp="2026-11-02 05:10:05"),
            commands_enabled=True,
            is_test=True,
        )
        live = car_status.build_current_status_response(app.ctrl)["status"]["sensors"]
    assert live["glass_reference"]["temperature_c"] == -1.0
    _set_now("2026-11-02T05:10:11+00:00")
    emitted = _post_http(
        app, _frame(_sensors(reference=_ok(-0.5)), timestamp="2026-11-02 05:10:10")
    )

    assert emitted["sensors"]["glass_reference"]["temperature_c"] == -0.5
    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert [r.glass_reference_temp_c for r in rows] == [-1.0, -0.5]


@pytest.mark.parametrize(
    "cabin",
    [
        _ok(150.0, humidity_pct=40.0),
        _ok(20.0, humidity_pct=120.0),
        _ok(float("nan"), humidity_pct=40.0),
        {"status": "ok", "temperature_c": "20", "humidity_pct": 40.0, "age_ms": 0},
    ],
)
def test_out_of_range_cabin_values_keep_the_previous_reading(app: Flask, cabin) -> None:
    _post_http(app, _frame(_sensors(cabin=_ok(4.0, humidity_pct=70.0)), temperature=4.0))
    _set_now("2026-11-02T05:10:06+00:00")

    emitted = _post_http(app, _frame(_sensors(cabin=cabin), timestamp="2026-11-02 05:10:05"))

    live = emitted["sensors"]["cabin"]
    assert live["temperature_c"] == 4.0
    assert live["humidity_pct"] == 70.0
    assert live["cached"] is True
    row = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)[-1]
    assert row.cabin_state == "invalid"
    assert row.cabin_temp_c is None


def test_out_of_range_legacy_cabin_temperature_is_unavailable_not_zero(app: Flask) -> None:
    emitted = _post_http(app, _frame({}, temperature=float("nan")))

    assert emitted["ambient_temp"] is None


def test_status_read_reports_readings_as_stale_once_they_age(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0, age_ms=0))))
    _set_now("2026-11-02T05:12:00+00:00")

    with app.app_context():
        response = car_status.build_current_status_response(app.ctrl)

    reference = response["status"]["sensors"]["glass_reference"]
    assert reference["state"] == "stale"
    assert reference["temperature_c"] == -1.0
    assert reference["age_s"] == 120.0


def test_restarted_server_rejects_replayed_older_samples(app: Flask) -> None:
    _post_http(app, _frame(_sensors(reference=_ok(-1.0, age_ms=0))))
    app.extensions.clear()  # process restart: in-memory tracker is gone
    _set_now("2026-11-02T05:10:30+00:00")

    emitted = _post_http(
        app, _frame(_sensors(reference=_ok(-7.0, age_ms=0)), timestamp="2026-11-02 05:09:30")
    )

    assert emitted["sensors"]["glass_reference"]["temperature_c"] == -1.0
    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert rows[-1].glass_reference_state == "out_of_order"


def _history_client(app: Flask):
    from flask import Blueprint
    from flask_login import LoginManager, UserMixin

    class _User(UserMixin):
        def __init__(self, user_id: str) -> None:
            self.id = user_id

    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.user_loader(_User)
    api_root = Blueprint("api_root", __name__, url_prefix="/api")
    api_root.register_blueprint(car_control.car_bp)
    app.register_blueprint(api_root)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "owner"
        session["_fresh"] = True
    return client


def test_history_returns_sensor_samples_for_the_local_day_without_inventing_values(
    app: Flask,
) -> None:
    _post_http(
        app,
        _frame(
            _sensors(
                reference=_ok(-1.0, age_ms=0),
                lower={"status": "missing", "temperature_c": None, "age_ms": None},
                cabin=_ok(0.0, age_ms=0, humidity_pct=88.0),
            )
        ),
    )
    _set_now("2026-11-02T23:30:01+00:00")  # 01:30 on 3 Nov in Helsinki
    _post_http(
        app, _frame(_sensors(reference=_ok(-6.0, age_ms=0)), timestamp="2026-11-02 23:30:00")
    )

    response = _history_client(app).get("/api/car_heater/history?date=2026-11-02")

    assert response.status_code == 200
    sensor_rows = response.get_json()["sensor_rows"]
    assert sensor_rows == [
        {"timestamp": "2026-11-02T05:10:00.000+00:00", "glass_reference_temp_c": -1.0},
        {
            "timestamp": "2026-11-02T05:10:00.000+00:00",
            "cabin_temp_c": 0.0,
            "cabin_humidity_pct": 88.0,
        },
    ]


def test_freshness_limit_is_configurable_and_inclusive(app: Flask, monkeypatch) -> None:
    monkeypatch.setenv("CAR_HEATER_SENSOR_MAX_AGE_S", "30")
    _set_now("2026-11-02T05:10:00+00:00")

    at_limit = _post_http(app, _frame(_sensors(reference=_ok(-1.0, age_ms=30_000))))
    over_limit = _post_http(app, _frame(_sensors(lower=_ok(-2.0, age_ms=30_001))))

    assert at_limit["sensors"]["glass_reference"]["state"] == "fresh"
    assert at_limit["sensors"]["glass_reference"]["max_age_s"] == 30.0
    assert "glass_comparison" not in over_limit["sensors"]
    rows = app.ctrl.get_car_heater_sensor_frames_between(DAY_START, DAY_END)
    assert [rows[0].glass_reference_state, rows[1].glass_comparison_state] == ["ok", "stale"]


@pytest.mark.parametrize("raw", ["0", "-5", "soon", "nan"])
def test_invalid_freshness_setting_falls_back_to_default(app: Flask, monkeypatch, raw: str) -> None:
    monkeypatch.setenv("CAR_HEATER_SENSOR_MAX_AGE_S", raw)

    emitted = _post_http(app, _frame(_sensors(reference=_ok(-1.0))))

    assert emitted["sensors"]["glass_reference"]["max_age_s"] == 60.0
