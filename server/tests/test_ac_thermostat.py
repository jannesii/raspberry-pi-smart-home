"""Unit tests for AC thermostat power-state reconciliation."""

from __future__ import annotations

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

    def get_status(self) -> dict[str, Any]:
        return dict(self.status)

    def turn_on(self) -> dict[str, Any]:
        self.commands.append(True)
        return {"ok": True}

    def turn_off(self) -> dict[str, Any]:
        self.commands.append(False)
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
) -> ACThermostat:
    monkeypatch.setenv("AC_TUYA_STATE_SETTLE_S", "30")
    return ACThermostat(
        ac=ac,  # type: ignore[arg-type]
        cfg=_cfg(),  # type: ignore[arg-type]
        ctrl=ctrl,  # type: ignore[arg-type]
        location="test",
        notify=lambda _event, _payload: None,
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


def test_stale_status_after_settle_window_records_external_change(
    monkeypatch: pytest.MonkeyPatch,
):
    """After the settle window expires, conflicting status is trusted again."""
    ac = DummyAC(is_on=False)
    ctrl = DummyCtrl()
    thermostat = _thermostat(monkeypatch, ac, ctrl)

    thermostat.turn_on()
    thermostat._local_power_settle_until = thermostat._now() - 1
    thermostat.step()

    assert thermostat.is_on is False
    assert ctrl.ac_events == [
        {"is_on": True, "source": "thermostat"},
        {"is_on": False, "source": "thermostat"},
    ]


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
