"""Regression tests for the standalone ESP32 websocket manager."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import redis

ROOT = Path(__file__).resolve().parents[1]
ESP32_WS_MAIN = ROOT / "esp32_ws" / "main.py"


class _FakeRedis:
    def ping(self) -> None:
        return None


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

    def close(self) -> None:
        self.closed += 1


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
