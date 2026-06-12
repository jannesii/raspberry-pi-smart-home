from __future__ import annotations

import pytest

from app.sockets._handlers import SocketEventHandler


class _SocketIOStub:
    def __init__(self):
        self.events = {}

    def on_event(self, event, handler):
        self.events[event] = handler


@pytest.fixture(autouse=True)
def reset_socket_handler_singleton():
    SocketEventHandler._instance = None
    yield
    SocketEventHandler._instance = None


def test_legacy_esp32_socket_events_are_not_registered():
    socketio = _SocketIOStub()

    handler = SocketEventHandler(socketio, object())

    assert "esp32_temphum" not in socketio.events
    assert "status" in socketio.events
    assert handler._extract_role({"role": "esp32"}) is None
