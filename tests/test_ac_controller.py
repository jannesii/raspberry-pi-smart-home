"""Unit tests for TinyTuya-backed AC controller behavior."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.services.ac.controller import ACController


class DummyDevice:
    """Minimal TinyTuya-like device test double."""

    def __init__(self, *, status_response=None, command_response=None):
        self.status_response = status_response
        self.command_response = command_response
        self.commands: list[tuple[int, object]] = []
        self.close_calls = 0
        self.socketPersistent = True
        self.raw_sent = SimpleNamespace(seqno=41, cmd=7)
        self.raw_recv = [SimpleNamespace(seqno=41, cmd=7)]

    @staticmethod
    def _next_response(response):
        if isinstance(response, list):
            return response.pop(0) if response else None
        return response

    def status(self):
        return self._next_response(self.status_response)

    def set_value(self, index: int, value: object):
        self.commands.append((index, value))
        return self._next_response(self.command_response)

    def close(self):
        self.close_calls += 1


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


def test_get_status_exposes_safe_transport_diagnostics():
    """Diagnostic reads should expose power type and request metadata."""
    device = DummyDevice(status_response={"dps": {"1": False}})
    controller = ACController(tinytuya_device=device)

    status, diagnostics = controller.get_status_with_diagnostics(correlation_id="ac-test-status")

    assert status == {"switch": False}
    assert diagnostics == {
        "method": "status",
        "attempt": 1,
        "correlation_id": "ac-test-status",
        "persistent": True,
        "sent_seq": 41,
        "sent_cmd": 7,
        "received_seq": 41,
        "received_cmd": 7,
        "sequence_match": True,
        "command_match": True,
        "response_power": False,
        "response_power_type": "bool",
        "response_error": None,
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


@pytest.mark.parametrize("power_dps", [{}, {"1": None}])
def test_get_status_omits_missing_dps_fields(
    power_dps: dict[str, object],
    caplog: pytest.LogCaptureFixture,
):
    """Partial device payloads must not invent false power-state values."""
    device = DummyDevice(
        status_response={
            "dps": {
                **power_dps,
                "4": "cold",
                "5": "high",
            }
        }
    )
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.DEBUG, logger="app.services.ac.controller"):
        status = controller.get_status()

    assert status == {
        "mode": "cold",
        "fan_speed_enum": "high",
    }
    assert "partial status response missing_dps=['1', '2', '3']" in caplog.text
    assert "partial status missing power DPS" in caplog.text


def test_missing_power_dps_warning_is_rate_limited(caplog: pytest.LogCaptureFixture):
    """Repeated partial power payloads should not flood production logs."""
    device = DummyDevice(
        status_response={"dps": {"4": "cold", "5": "high"}},
    )
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.WARNING, logger="app.services.ac.controller"):
        controller.get_status()
        controller.get_status()

    assert caplog.text.count("partial status missing power DPS") == 1


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


def test_turn_on_retries_transient_payload_errors():
    """Malformed TinyTuya responses should reconnect and retry idempotent writes."""
    device = DummyDevice(
        command_response=[
            {"Error": "Unexpected Payload from Device", "Err": "904", "Payload": None},
            {"Error": "Invalid JSON Response from Device", "Err": "900", "Payload": ""},
            {"dps": {"1": True}},
        ]
    )
    controller = ACController(
        tinytuya_device=device,
        retry_attempts=3,
        retry_delay_s=0,
    )

    result = controller.turn_on()

    assert result == {"dps": {"1": True}}
    assert device.commands == [(controller.POWER, True)] * 3
    assert device.close_calls == 2


def test_power_command_logs_correlation_and_response_metadata(
    caplog: pytest.LogCaptureFixture,
):
    """Power command diagnostics should be traceable without full payload logging."""
    device = DummyDevice(command_response={"dps": {"1": True}})
    controller = ACController(tinytuya_device=device)

    with caplog.at_level(logging.INFO, logger="app.services.ac.controller"):
        controller.turn_on(correlation_id="ac-test-command")

    assert "power command response correlation_id=ac-test-command" in caplog.text
    assert "response_power=True" in caplog.text
    assert "response_power_type=bool" in caplog.text
    assert "sequence_match=True" in caplog.text


def test_get_status_retries_transient_payload_error():
    """Status reads should recover from a single malformed device response."""
    device = DummyDevice(
        status_response=[
            {"Error": "Unexpected Payload from Device", "Err": "904", "Payload": None},
            {"dps": {"1": False, "4": "cold", "5": "low"}},
        ]
    )
    controller = ACController(
        tinytuya_device=device,
        retry_attempts=2,
        retry_delay_s=0,
    )

    status = controller.get_status()

    assert status["switch"] is False
    assert status["mode"] == "cold"
    assert status["fan_speed_enum"] == "low"
    assert device.close_calls == 1


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
