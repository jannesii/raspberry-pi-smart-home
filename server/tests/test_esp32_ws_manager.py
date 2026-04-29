"""Regression tests for the standalone ESP32 websocket manager."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import redis

ROOT = Path(__file__).resolve().parents[1]
ESP32_WS_MAIN = ROOT / "esp32_ws" / "main.py"


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def ping(self) -> None:
        return None

    def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


class _FakeThread:
    def __init__(self, *args, **kwargs) -> None:
        self._alive = False

    def start(self) -> None:
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self._alive = False


class _DummyWS:
    def __init__(self) -> None:
        self.closed = 0
        self.sent: list[str] = []

    def close(self) -> None:
        self.closed += 1

    def send(self, payload: str) -> None:
        self.sent.append(payload)


def _load_esp32_ws_main(monkeypatch):
    monkeypatch.setattr(redis, "from_url", lambda *args, **kwargs: _FakeRedis())
    monkeypatch.setattr(threading, "Thread", _FakeThread)

    module_name = "test_esp32_ws_main"
    spec = importlib.util.spec_from_file_location(module_name, ESP32_WS_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_stale_unregister_does_not_drop_replacement_connection(monkeypatch) -> None:
    module = _load_esp32_ws_main(monkeypatch)
    manager = module.ESP32WebSocketManager()

    first_ws = _DummyWS()
    first_conn = manager.register_connection("car_heater_esp32", first_ws)

    second_ws = _DummyWS()
    second_conn = manager.register_connection("car_heater_esp32", second_ws)

    assert manager.get_connection("car_heater_esp32") is second_conn
    assert first_ws.closed == 1

    manager.unregister_connection("car_heater_esp32", ws=first_ws)

    assert manager.get_connection("car_heater_esp32") is second_conn
    assert manager.get_connection("car_heater_esp32") is not first_conn

    manager.unregister_connection("car_heater_esp32", ws=second_ws)

    assert manager.get_connection("car_heater_esp32") is None


def test_temperature_connection_and_rpc_command_routing(monkeypatch) -> None:
    module = _load_esp32_ws_main(monkeypatch)
    manager = module.ESP32WebSocketManager()

    temp_ws = _DummyWS()
    manager.register_connection("temperature_kitchen", temp_ws, device_type="temperature")
    heater_ws = _DummyWS()
    manager.register_connection("car_heater_esp32", heater_ws, device_type="car_heater")

    manager._forward_command_to_temperature_esp32(
        {
            "device_id": "temperature_kitchen",
            "request_id": "req1",
            "action": "read_now",
        }
    )

    assert len(temp_ws.sent) == 1
    sent_payload = json.loads(temp_ws.sent[0])
    assert sent_payload["type"] == "rpc_request"
    assert sent_payload["action"] == "read_now"
    assert heater_ws.sent == []


def test_temperature_messages_publish_to_dedicated_channels(monkeypatch) -> None:
    module = _load_esp32_ws_main(monkeypatch)
    manager = module.ESP32WebSocketManager()
    fake_redis = _FakeRedis()
    manager._redis = fake_redis

    telemetry = {
        "type": "temperature_reading",
        "device_id": "temperature_kitchen",
        "temperature_c": 21.5,
        "humidity_pct": 41.0,
    }
    rpc_result = {
        "type": "rpc_response",
        "device_id": "temperature_kitchen",
        "request_id": "req1",
        "ok": True,
    }

    manager.publish_temperature_telemetry(telemetry)
    manager.publish_temperature_rpc_result(rpc_result)

    assert fake_redis.published[0][0] == module.CHANNEL_TEMPERATURE_TELEMETRY
    assert fake_redis.published[1][0] == module.CHANNEL_TEMPERATURE_RPC_RESULTS
