"""Unit tests for AC thermostat power-state reconciliation."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.services.ac.thermostat import ACThermostat

if TYPE_CHECKING:
    import pytest


class DummyAC:
    """Small AC device test double with mutable status."""

    def __init__(self, *, is_on: bool = False) -> None:
        self.status: dict[str, Any] = {
            "switch": is_on,
            "mode": "cold",
            "fan_speed_enum": "low",
        }
        self.commands: list[bool] = []
        self.command_correlations: list[str | None] = []
        self.status_diagnostics: dict[str, Any] = {
            "sent_seq": 8,
            "sent_cmd": 10,
            "received_seq": 8,
            "received_cmd": 10,
            "sequence_match": True,
            "command_match": True,
            "persistent": True,
            "attempt": 1,
        }

    def get_status(self) -> dict[str, Any]:
        return dict(self.status)

    def get_status_with_diagnostics(
        self,
        *,
        correlation_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        diagnostics = {**self.status_diagnostics, "correlation_id": correlation_id}
        return dict(self.status), diagnostics

    def turn_on(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        self.commands.append(True)
        self.command_correlations.append(correlation_id)
        return {"ok": True}

    def turn_off(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        self.commands.append(False)
        self.command_correlations.append(correlation_id)
        return {"ok": True}

    def set_temperature(self, _celsius: int) -> dict[str, Any]:
        return {"ok": True}


class DummyCtrl:
    """Controller test double for thermostat persistence and events."""

    def __init__(self) -> None:
        self.saved_configs: list[dict[str, Any]] = []
        self.ac_events: list[dict[str, Any]] = []
        self.messages: list[str] = []

    def save_thermostat_conf(self, **kwargs: Any) -> None:
        self.saved_configs.append(kwargs)

    def record_ac_event(self, is_on: bool, source: str, **_kwargs: Any) -> None:
        self.ac_events.append({"is_on": is_on, "source": source})

    def log_message(self, message: str, **_kwargs: Any) -> None:
        self.messages.append(message)

    def get_last_esp32_temphum_for_location(self, _location: str) -> None:
        return None


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        sleep_active=False,
        sleep_start=None,
        sleep_stop=None,
        sleep_weekly=None,
        control_locations=None,
        target_temp=24.5,
        pos_hysteresis=0.5,
        neg_hysteresis=0.5,
        thermo_active=False,
        min_on_s=240,
        min_off_s=240,
        poll_interval_s=0,
        smooth_window=1,
        max_stale_s=120,
        current_phase="off",
        phase_started_at=None,
    )


def _thermostat(
    monkeypatch: pytest.MonkeyPatch,
    ac: DummyAC,
    ctrl: DummyCtrl,
    *,
    cfg: SimpleNamespace | None = None,
    notify=None,
) -> ACThermostat:
    monkeypatch.setenv("AC_TUYA_STATE_SETTLE_S", "30")
    return ACThermostat(
        ac=ac,  # type: ignore[arg-type]
        cfg=cfg or _cfg(),  # type: ignore[arg-type]
        ctrl=ctrl,  # type: ignore[arg-type]
        location="test",
        notify=notify or (lambda _event, _payload: None),
    )


def test_local_on_command_ignores_stale_off_status(monkeypatch: pytest.MonkeyPatch):
    """A stale OFF read right after turn_on must not flip internal state."""
    ac = DummyAC(is_on=False)
    ctrl = DummyCtrl()
    thermostat = _thermostat(monkeypatch, ac, ctrl)

    thermostat.turn_on()
    thermostat.step()

    assert thermostat.is_on is True
    assert ac.commands == [True]
    assert ctrl.ac_events == [{"is_on": True, "source": "thermostat"}]
    assert ac.command_correlations[0]


def test_stale_status_after_settle_window_records_external_change(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """After the settle window expires, conflicting status is trusted again."""
    ac = DummyAC(is_on=False)
    ctrl = DummyCtrl()
    thermostat = _thermostat(monkeypatch, ac, ctrl)

    thermostat.turn_on()
    thermostat._local_power_settle_until = thermostat._now() - 1
    with caplog.at_level(logging.WARNING, logger="app.services.ac.thermostat._thermostat"):
        thermostat.step()

    assert thermostat.is_on is False
    assert ctrl.ac_events == [
        {"is_on": True, "source": "thermostat"},
        {"is_on": False, "source": "device"},
    ]
    assert "action=accepted_after_settle" in caplog.text
    assert "raw_switch=False raw_switch_type=bool" in caplog.text
    assert f"correlation_id={ac.command_correlations[0]}" in caplog.text


def test_confirming_status_clears_local_power_guard(monkeypatch: pytest.MonkeyPatch):
    """A matching device status should clear the local command guard early."""
    ac = DummyAC(is_on=False)
    ctrl = DummyCtrl()
    thermostat = _thermostat(monkeypatch, ac, ctrl)

    thermostat.turn_on()
    ac.status["switch"] = True
    thermostat.step()

    assert thermostat.is_on is True
    assert thermostat._local_power_expected_on is None
    assert thermostat._local_power_settle_until == 0.0


def test_partial_startup_status_uses_persisted_phase(monkeypatch: pytest.MonkeyPatch):
    """Missing startup switch DPS should preserve the persisted power phase."""
    ac = DummyAC(is_on=False)
    ac.status = {"mode": "cold", "fan_speed_enum": "low"}
    ctrl = DummyCtrl()
    cfg = _cfg()
    cfg.current_phase = "on"

    thermostat = _thermostat(monkeypatch, ac, ctrl, cfg=cfg)

    assert thermostat.is_on is True
    assert thermostat.get_power_status_payload()["state_source"] == "persisted"


def test_manual_power_event_and_ui_payload_share_correlation(
    monkeypatch: pytest.MonkeyPatch,
):
    """Manual commands should retain source and correlation through persistence and UI."""
    ac = DummyAC(is_on=False)
    ctrl = DummyCtrl()
    notifications: list[tuple[str, dict[str, Any]]] = []
    thermostat = _thermostat(
        monkeypatch,
        ac,
        ctrl,
        notify=lambda event, payload: notifications.append((event, payload)),
    )

    thermostat.set_power(True)

    correlation_id = ac.command_correlations[-1]
    assert correlation_id
    assert ctrl.ac_events == [{"is_on": True, "source": "manual"}]
    assert notifications[-1][0] == "ac_status"
    assert notifications[-1][1]["state_source"] == "manual"
    assert notifications[-1][1]["state_correlation_id"] == correlation_id
