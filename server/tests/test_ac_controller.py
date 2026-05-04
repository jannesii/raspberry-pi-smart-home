"""Unit tests for TinyTuya-backed AC controller behavior."""

from __future__ import annotations

import logging

import pytest

from app.services.ac.controller import ACController


class DummyDevice:
    """Minimal TinyTuya-like device test double."""

    def __init__(self, *, status_response=None, command_response=None):
        self.status_response = status_response
        self.command_response = command_response
        self.commands: list[tuple[int, object]] = []

    def status(self):
        return self.status_response

    def set_value(self, index: int, value: object):
        self.commands.append((index, value))
        return self.command_response


def test_get_status_maps_known_dps_fields():
    """Controller should normalize the TinyTuya DPS payload into stable keys."""
    device = DummyDevice(
        status_response={
            "dps": {
                "1": True,
                "2": 23,
                "3": 21,
                "4": "cold",
                "5": "high",
            }
        }
    )
    controller = ACController(tinytuya_device=device)

    status = controller.get_status()

    assert status == {
        "switch": True,
        "mode": "cold",
        "fan_speed_enum": "high",
        "set_temperature": 23,
        "temp_current": 21,
    }


def test_get_status_returns_empty_dict_for_error_payload(caplog: pytest.LogCaptureFixture):
    """Explicit TinyTuya error payloads should not trigger logging formatter failures."""
    device = DummyDevice(
        status_response={
            "Error": "Network Error: Unable to Connect",
            "Err": "901",
            "Payload": None,
        }
    )
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.WARNING):
        status = controller.get_status()

    assert status == {}
    assert "status request failed err=901" in caplog.text


def test_get_status_returns_empty_dict_for_empty_dps(caplog: pytest.LogCaptureFixture):
    """Empty DPS responses should be handled as a normal failure path."""
    device = DummyDevice(status_response={"dps": []})
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.WARNING):
        status = controller.get_status()

    assert status == {}
    assert "empty status response" in caplog.text


def test_turn_on_raises_runtime_error_for_device_error():
    """Command failures should bubble up with the TinyTuya error details."""
    device = DummyDevice(
        command_response={
            "Error": "Check device key or version",
            "Err": "914",
            "Payload": None,
        }
    )
    controller = ACController(tinytuya_device=device)

    with pytest.raises(RuntimeError, match=r"Check device key or version \(914\)"):
        controller.turn_on()

    assert device.commands == [(controller.POWER, True)]


def test_control_operations_emit_debug_logs(caplog: pytest.LogCaptureFixture):
    """Each public control operation should log a concise debug entry."""
    device = DummyDevice(command_response={"ok": True})
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.DEBUG, logger="app.services.ac.controller"):
        controller.turn_on()
        controller.turn_off()
        controller.set_mode(" COLD ")
        controller.set_fan_speed(" HIGH ")
        controller.set_temperature(23)

    assert device.commands == [
        (controller.POWER, True),
        (controller.POWER, False),
        (controller.MODE, "cold"),
        (controller.FAN, "high"),
        (controller.TEMP_SET, 23),
    ]
    assert "control turn_on requested" in caplog.text
    assert "control turn_off requested" in caplog.text
    assert "control set_mode requested mode=cold" in caplog.text
    assert "control set_fan_speed requested speed=high" in caplog.text
    assert "control set_temperature requested celsius=23" in caplog.text
