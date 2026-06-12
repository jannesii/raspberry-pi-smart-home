"""Tests for car heater log snapshot event wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from flask import Flask

from app.blueprints.api.car_heater import control as car_control, status as car_status
from app.services.esp32_redis_bridge import ESP32RedisBridge


class _DummySIOHandler:
    def __init__(self) -> None:
        self.view_events: list[tuple[str, dict[str, Any]]] = []
        self.status_events: list[
            tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]
        ] = []

    def emit_to_views(self, event: str, payload: dict[str, Any]) -> None:
        self.view_events.append((event, payload))

    def emit_car_heater_status_to_views(
        self,
        status_payload: dict[str, Any],
        command_status: dict[str, Any] | None,
        charge_mode_state: dict[str, Any] | None,
    ) -> None:
        self.status_events.append((status_payload, command_status, charge_mode_state))


class _DummySocketIO:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


def test_status_handler_emits_car_heater_logs_event_with_http_source(monkeypatch) -> None:
    app = Flask(__name__)
    app.ctrl = object()
    app.sio_handler = _DummySIOHandler()

    monkeypatch.setattr(
        car_control,
        "process_commands",
        lambda data, car: ([], None, None),
    )

    payload = {
        "timestamp": "2026-02-22 10:10:10",
        "shelly_connected": False,
        "temperature": -6.4,
        "logs": " line one\nline two ",
        "device_id": "car_heater_esp32",
    }

    with app.app_context():
        response, status_code = car_status.handle_status_update_request(
            payload, commands_enabled=True
        )

    assert status_code == 200
    assert response.get_json() == []
    assert len(app.sio_handler.status_events) == 1

    log_events = [event for event in app.sio_handler.view_events if event[0] == "car_heater_logs"]
    assert len(log_events) == 1
    logs_payload = log_events[0][1]
    assert logs_payload["logs"] == "line one\nline two"
    assert logs_payload["source"] == "http_status"
    assert logs_payload["device_id"] == "car_heater_esp32"
    assert "received_ts" in logs_payload


def test_status_handler_skips_empty_logs_payload(monkeypatch) -> None:
    app = Flask(__name__)
    app.ctrl = object()
    app.sio_handler = _DummySIOHandler()

    monkeypatch.setattr(
        car_control,
        "process_commands",
        lambda data, car: ([], None, None),
    )

    payload = {
        "timestamp": "2026-02-22 10:10:10",
        "shelly_connected": False,
        "temperature": -6.4,
        "logs": "   ",
    }

    with app.app_context():
        response, status_code = car_status.handle_status_update_request(
            payload, commands_enabled=True
        )

    assert status_code == 200
    assert response.get_json() == []
    assert len(app.sio_handler.status_events) == 1

    log_events = [event for event in app.sio_handler.view_events if event[0] == "car_heater_logs"]
    assert log_events == []


def test_esp32_redis_bridge_emits_status_and_logs_event() -> None:
    app = Flask(__name__)
    app.ctrl = object()
    socketio = _DummySocketIO()
    sio_handler = _DummySIOHandler()
    app.sio_handler = sio_handler
    bridge = ESP32RedisBridge(app=app, socketio=socketio, sio_handler=sio_handler)

    bridge._emit_status(
        {
            "temperature": -3.2,
            "timestamp": "2026-02-22 10:10:10",
            "device_id": "car_heater_esp32",
            "logs": "ESP line 1\nESP line 2",
        }
    )

    assert len(sio_handler.status_events) == 1
    assert len(sio_handler.view_events) == 1
    assert sio_handler.view_events[0][0] == "car_heater_logs"

    logs_payload = sio_handler.view_events[0][1]
    assert logs_payload["source"] == "ws_status"
    assert logs_payload["device_id"] == "car_heater_esp32"
    assert logs_payload["logs"] == "ESP line 1\nESP line 2"
    assert "received_ts" in logs_payload


def test_esp32_redis_bridge_skips_logs_event_when_missing() -> None:
    app = Flask(__name__)
    app.ctrl = object()
    socketio = _DummySocketIO()
    sio_handler = _DummySIOHandler()
    app.sio_handler = sio_handler
    bridge = ESP32RedisBridge(app=app, socketio=socketio, sio_handler=sio_handler)

    bridge._emit_status({"temperature": -3.2, "timestamp": "2026-02-22 10:10:10"})

    assert len(sio_handler.status_events) == 1
    assert sio_handler.view_events == []


class _TempCtrl:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def get_thermostat_conf(self):
        return None

    def record_esp32_temphum(self, location, temp, hum, ac_on=None):
        row = {
            "location": location,
            "temperature": float(temp),
            "humidity": float(hum),
            "ac_on": ac_on,
        }
        self.saved.append(row)
        return SimpleNamespace(**row)


def test_esp32_redis_bridge_persists_temperature_telemetry(monkeypatch) -> None:
    app = Flask(__name__)
    app.ctrl = _TempCtrl()
    app.sio_handler = _DummySIOHandler()
    socketio = _DummySocketIO()
    bridge = ESP32RedisBridge(app=app, socketio=socketio, sio_handler=app.sio_handler)

    monkeypatch.setattr(
        "app.blueprints.api.esp32_api._schedule_sensor_side_effects",
        lambda **_kwargs: None,
    )

    bridge._handle_temperature_telemetry(
        {
            "type": "temperature_reading",
            "device_id": "temperature_kitchen",
            "location": "Keittiö",
            "temperature_c": 21.5,
            "humidity_pct": 42.0,
        }
    )

    assert app.ctrl.saved == [
        {
            "location": "Keittiö",
            "temperature": 21.5,
            "humidity": 42.0,
            "ac_on": None,
        }
    ]
    assert app.sio_handler.view_events == [
        (
            "esp32_temphum",
            {
                "location": "Keittiö",
                "temperature": 21.5,
                "humidity": 42.0,
                "ac_on": None,
            },
        )
    ]


def test_esp32_redis_bridge_emits_temperature_rpc_result() -> None:
    app = Flask(__name__)
    socketio = _DummySocketIO()
    sio_handler = _DummySIOHandler()
    bridge = ESP32RedisBridge(app=app, socketio=socketio, sio_handler=sio_handler)

    bridge._emit_temperature_rpc_result(
        {
            "type": "rpc_response",
            "device_id": "temperature_kitchen",
            "request_id": "req1",
            "ok": True,
        }
    )

    assert sio_handler.view_events == [
        (
            "esp32_temperature_rpc_result",
            {
                "type": "rpc_response",
                "device_id": "temperature_kitchen",
                "request_id": "req1",
                "ok": True,
            },
        )
    ]
