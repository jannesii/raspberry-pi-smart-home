"""
AC Thermostat - Main orchestrator.

The main ACThermostat class that coordinates all thermostat activities.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytz

from .notifier import NotificationEmitter
from .sleep_manager import SleepManager
from .temp_reader import TemperatureReader
from .time_utils import compute_phase_duration, parse_iso_to_epoch

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.core import Controller, ThermostatConf
    from app.services.ac import ACController

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("thermo: invalid %s=%r, using default=%s", name, raw, default)
        return default


class ACThermostat:
    """Main thermostat controller using external temperature source."""

    DEFAULT_LOCAL_POWER_SETTLE_S = 120.0

    def __init__(
        self,
        ac: ACController,
        cfg: ThermostatConf,
        ctrl: Controller,
        location: str,
        notify: Callable[[str, dict[str, Any]], None] | None = None,
        winter: bool = False,
    ) -> None:
        self.winter = winter
        self.ac = ac
        self.cfg = cfg
        self.ctrl = ctrl
        self.location = location
        self.notify = notify
        self.tz = pytz.timezone("Europe/Helsinki")
        self._local_power_expected_on: bool | None = None
        self._local_power_settle_until: float = 0.0
        self._local_power_commanded_at: float = 0.0
        self._local_power_correlation_id: str | None = None
        self._local_power_source: str | None = None
        self._local_power_mismatch_count = 0
        self._state_correlation_id = self._new_correlation_id()
        self._state_source = "startup"
        self._state_observed_at = datetime.now(self.tz).isoformat()

        # Initialize AC state
        ac_status, startup_diagnostics = (
            self._read_device_status(self._state_correlation_id) if not winter else ({}, {})
        )
        persisted_phase = getattr(cfg, "current_phase", None)
        if winter:
            self._is_on = False
            self._state_source = "winter"
            logger.debug(
                "thermo: startup power state correlation_id=%s source=winter is_on=False",
                self._state_correlation_id,
            )
        elif "switch" in ac_status and ac_status.get("switch") is not None:
            raw_startup_switch = ac_status["switch"]
            self._is_on = bool(raw_startup_switch)
            self._state_source = "device"
            logger.info(
                "thermo: startup power state correlation_id=%s source=device raw_switch=%r "
                "raw_switch_type=%s is_on=%s diagnostics=%s",
                self._state_correlation_id,
                raw_startup_switch,
                type(raw_startup_switch).__name__,
                self._is_on,
                startup_diagnostics,
            )
        elif persisted_phase in {"on", "off"}:
            self._is_on = persisted_phase == "on"
            self._state_source = "persisted"
            logger.warning(
                "thermo: startup status missing switch correlation_id=%s; "
                "using persisted_phase=%s diagnostics=%s",
                self._state_correlation_id,
                persisted_phase,
                startup_diagnostics,
            )
        else:
            self._is_on = False
            self._state_source = "fallback"
            logger.warning(
                "thermo: startup status missing switch and persisted phase "
                "correlation_id=%s; defaulting OFF diagnostics=%s",
                self._state_correlation_id,
                startup_diagnostics,
            )
        self.mode: str | None = (
            ac_status.get("mode", "cold") if isinstance(ac_status, dict) else None
        )
        self.fan_speed: str | None = (
            ac_status.get("fan_speed_enum", "low") if isinstance(ac_status, dict) else None
        )
        self._enabled: bool = bool(getattr(cfg, "thermo_active", True)) if not winter else False
        self._last_change_ts: float = 0.0
        self._local_power_settle_s = max(
            0.0,
            _env_float("AC_TUYA_STATE_SETTLE_S", self.DEFAULT_LOCAL_POWER_SETTLE_S),
        )

        # Track persisted start ISO for the current phase
        self._phase_started_at_iso: str | None = getattr(cfg, "phase_started_at", None)

        # Initialize submodules
        self._sleep = SleepManager(cfg, self.tz, self._emit_sleep_status)
        self._sleep.is_sleep_time = self._sleep.is_sleep_window_now()
        self._temp_reader = TemperatureReader(ctrl, cfg, location, self.tz)
        self._emitter = NotificationEmitter(notify, cfg)

        # Initialize phase timing
        self._init_phase_timing()

        logger.debug(
            "thermo: init %s is_on=%s mode=%s fan=%s",
            cfg,
            self._is_on,
            self.mode,
            self.fan_speed,
        )
        logger.debug("thermo: local power settle window=%ss", self._local_power_settle_s)

    def _init_phase_timing(self) -> None:
        """Initialize phase timing from persisted state."""
        now_epoch = time.time()
        started_epoch = parse_iso_to_epoch(self._phase_started_at_iso, self.tz)
        logger.debug(
            "thermo: parsed phase_started_at=%s -> %s",
            self._phase_started_at_iso,
            started_epoch,
        )

        # Validate persisted phase matches actual state
        if self._is_on:
            if getattr(self.cfg, "current_phase", None) != "on" or started_epoch is None:
                self._phase_started_at_iso = datetime.fromtimestamp(
                    now_epoch, tz=self.tz
                ).isoformat()
                self._persist_conf()
        else:
            if getattr(self.cfg, "current_phase", None) != "off" or started_epoch is None:
                self._phase_started_at_iso = datetime.fromtimestamp(
                    now_epoch, tz=self.tz
                ).isoformat()
                self._persist_conf()

        # Set last-change timestamp from phase start
        started_epoch = parse_iso_to_epoch(self._phase_started_at_iso, self.tz)
        if started_epoch is not None:
            self._last_change_ts = min(now_epoch, float(started_epoch))
        else:
            self._last_change_ts = now_epoch

        # Log phase age
        phase_lbl = "ON" if self._is_on else "OFF"
        age_min = compute_phase_duration(self._phase_started_at_iso, self.tz) or 0
        logger.info(
            "thermo: current phase=%s age=%d min since %s",
            phase_lbl,
            age_min,
            self._phase_started_at_iso,
        )

    def _now(self) -> float:
        return time.time()

    @staticmethod
    def _new_correlation_id() -> str:
        """Create a compact identifier for one AC state transition chain."""
        return f"ac-{uuid4().hex[:12]}"

    def _read_device_status(
        self,
        correlation_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read AC status with transport metadata when the controller supports it."""
        reader = getattr(self.ac, "get_status_with_diagnostics", None)
        if callable(reader):
            status, diagnostics = reader(correlation_id=correlation_id)
            return (
                status if isinstance(status, dict) else {},
                diagnostics if isinstance(diagnostics, dict) else {},
            )
        status = self.ac.get_status()
        return (status if isinstance(status, dict) else {}, {})

    def _can_turn_on(self) -> bool:
        ok = (
            self._now() - self._last_change_ts
        ) >= self.cfg.min_off_s and not self._sleep.is_sleep_window_now()
        logger.debug("thermo: _can_turn_on=%s", ok)
        return ok

    def _can_turn_off(self) -> bool:
        ok = (self._now() - self._last_change_ts) >= self.cfg.min_on_s
        logger.debug("thermo: _can_turn_off=%s", ok)
        return ok

    def _persist_conf(self) -> None:
        """Persist current thermostat config to DB."""
        try:
            self.ctrl.save_thermostat_conf(
                sleep_active=self.cfg.sleep_active,
                sleep_start=self.cfg.sleep_start,
                sleep_stop=self.cfg.sleep_stop,
                sleep_weekly=getattr(self.cfg, "sleep_weekly", None),
                control_locations=getattr(self.cfg, "control_locations", None),
                target_temp=self.cfg.target_temp,
                pos_hysteresis=self.cfg.pos_hysteresis,
                neg_hysteresis=self.cfg.neg_hysteresis,
                thermo_active=self._enabled,
                min_on_s=int(self.cfg.min_on_s),
                min_off_s=int(self.cfg.min_off_s),
                poll_interval_s=int(self.cfg.poll_interval_s),
                smooth_window=int(self.cfg.smooth_window),
                max_stale_s=self.cfg.max_stale_s,
                current_phase=("on" if self._is_on else "off"),
                phase_started_at=self._phase_started_at_iso,
            )
        except Exception as e:
            logger.debug("thermo: persist conf failed: %s", e)

    def _record_transition(
        self,
        *,
        source: str,
        correlation_id: str | None,
    ) -> int | None:
        """Record phase transition, persist, return previous phase duration in minutes."""
        minutes = compute_phase_duration(self._phase_started_at_iso, self.tz)

        # Log AC on/off event into DB
        try:
            self.ctrl.record_ac_event(
                is_on=bool(self._is_on),
                source=source,
                note=(f"correlation_id={correlation_id}" if correlation_id else None),
            )
        except Exception as e:
            logger.debug("thermo: failed to record ac_event: %s", e)

        # Set new phase start
        self._phase_started_at_iso = datetime.now(self.tz).isoformat()
        self._persist_conf()
        return minutes

    def _record_external_state(
        self,
        new_on: bool,
        *,
        correlation_id: str | None,
    ) -> None:
        """Update counters on external device state changes without issuing commands."""
        if new_on == self._is_on:
            return
        self._is_on = new_on
        self._set_state_metadata(source="device", correlation_id=correlation_id)
        self._record_transition(source="device", correlation_id=correlation_id)
        self._last_change_ts = self._now()
        self._emit_power_status()

    def _mark_local_power_command(
        self,
        expected_on: bool,
        *,
        correlation_id: str,
        source: str,
    ) -> None:
        """Start the settle window for a locally issued power command."""
        now = self._now()
        self._local_power_expected_on = expected_on
        self._local_power_commanded_at = now
        self._local_power_settle_until = now + self._local_power_settle_s
        self._local_power_correlation_id = correlation_id
        self._local_power_source = source
        self._local_power_mismatch_count = 0
        logger.debug(
            "thermo: local power command correlation_id=%s source=%s expected_on=%s "
            "settle_until=%.3f",
            correlation_id,
            source,
            expected_on,
            self._local_power_settle_until,
        )

    def _clear_local_power_command(self, reason: str) -> None:
        """Clear the local command guard once device status is reliable again."""
        if self._local_power_expected_on is None:
            return
        logger.debug(
            "thermo: clearing local power command guard reason=%s correlation_id=%s "
            "expected_on=%s mismatches=%s",
            reason,
            self._local_power_correlation_id,
            self._local_power_expected_on,
            self._local_power_mismatch_count,
        )
        self._local_power_expected_on = None
        self._local_power_settle_until = 0.0
        self._local_power_commanded_at = 0.0
        self._local_power_correlation_id = None
        self._local_power_source = None
        self._local_power_mismatch_count = 0

    def _should_ignore_external_state(self, reported_on: bool, now: float) -> bool:
        """Return whether a contradictory device read is likely post-command lag."""
        expected_on = self._local_power_expected_on
        if expected_on is None:
            return False
        if now >= self._local_power_settle_until:
            return False
        if reported_on == expected_on:
            self._clear_local_power_command("device_confirmed")
            return False
        logger.debug(
            "thermo: ignoring device state during local command settle "
            "reported_on=%s expected_on=%s remaining_s=%.1f",
            reported_on,
            expected_on,
            self._local_power_settle_until - now,
        )
        return True

    def _set_state_metadata(
        self,
        *,
        source: str,
        correlation_id: str | None,
    ) -> None:
        """Update diagnostic metadata associated with the in-memory power state."""
        self._state_source = source
        self._state_correlation_id = correlation_id or self._new_correlation_id()
        self._state_observed_at = datetime.now(self.tz).isoformat()
        logger.debug(
            "thermo: state metadata source=%s correlation_id=%s observed_at=%s",
            self._state_source,
            self._state_correlation_id,
            self._state_observed_at,
        )

    def get_power_status_payload(self) -> dict[str, Any]:
        """Return power state with source and correlation metadata for UI clients."""
        return {
            "is_on": bool(self._is_on),
            "state_source": self._state_source,
            "state_correlation_id": self._state_correlation_id,
            "state_observed_at": self._state_observed_at,
        }

    def _emit_power_status(self) -> None:
        """Emit the current power state and its diagnostic metadata."""
        self._emitter.emit_status(**self.get_power_status_payload())

    def _log_device_power_observation(
        self,
        *,
        level: int,
        action: str,
        raw_switch: Any,
        diagnostics: dict[str, Any],
        now: float,
        correlation_id: str | None,
    ) -> None:
        """Log one safe, correlation-friendly device power observation."""
        command_age_s = (
            now - self._local_power_commanded_at if self._local_power_commanded_at > 0 else None
        )
        settle_remaining_s = (
            max(0.0, self._local_power_settle_until - now)
            if self._local_power_expected_on is not None
            else None
        )
        logger.log(
            level,
            "thermo: device power observation action=%s correlation_id=%s "
            "raw_switch=%r raw_switch_type=%s reported_on=%s internal_on=%s "
            "expected_on=%s command_source=%s command_age_s=%s "
            "settle_remaining_s=%s mismatch_count=%s sent_seq=%s sent_cmd=%s "
            "received_seq=%s received_cmd=%s sequence_match=%s command_match=%s "
            "persistent=%s attempt=%s",
            action,
            correlation_id,
            raw_switch,
            type(raw_switch).__name__,
            bool(raw_switch),
            self._is_on,
            self._local_power_expected_on,
            self._local_power_source,
            round(command_age_s, 1) if command_age_s is not None else None,
            round(settle_remaining_s, 1) if settle_remaining_s is not None else None,
            self._local_power_mismatch_count,
            diagnostics.get("sent_seq"),
            diagnostics.get("sent_cmd"),
            diagnostics.get("received_seq"),
            diagnostics.get("received_cmd"),
            diagnostics.get("sequence_match"),
            diagnostics.get("command_match"),
            diagnostics.get("persistent"),
            diagnostics.get("attempt"),
        )

    def _thresholds(self) -> tuple[float, float]:
        """Return (on_at, off_at) temperature thresholds."""
        on_at = self.cfg.target_temp + float(self.cfg.pos_hysteresis)
        off_at = self.cfg.target_temp - float(self.cfg.neg_hysteresis)
        return on_at, off_at

    # =========================================================================
    # Power control
    # =========================================================================

    def turn_on(self, *, source: str = "thermostat") -> int | None:
        """Turn AC on and record transition."""
        correlation_id = self._new_correlation_id()
        self.ac.turn_on(correlation_id=correlation_id)
        self._is_on = True
        self._set_state_metadata(source=source, correlation_id=correlation_id)
        self._mark_local_power_command(
            True,
            correlation_id=correlation_id,
            source=source,
        )
        return self._record_transition(source=source, correlation_id=correlation_id)

    def turn_off(self, *, source: str = "thermostat") -> int | None:
        """Turn AC off and record transition."""
        correlation_id = self._new_correlation_id()
        self.ac.turn_off(correlation_id=correlation_id)
        self._is_on = False
        self._set_state_metadata(source=source, correlation_id=correlation_id)
        self._mark_local_power_command(
            False,
            correlation_id=correlation_id,
            source=source,
        )
        return self._record_transition(source=source, correlation_id=correlation_id)

    def set_power(self, on: bool) -> None:
        """Manually set AC power state."""
        if on:
            self.turn_on(source="manual")
        else:
            self.turn_off(source="manual")
        self._last_change_ts = self._now()
        self._emit_power_status()

    # =========================================================================
    # AC mode/fan control
    # =========================================================================

    def set_mode(self, mode: str) -> None:
        """Set AC mode immediately."""
        mode_l = str(mode).strip().lower()
        self.ac.set_mode(mode_l)
        self.mode = mode_l
        self._emitter.emit_ac_state(self.mode, self.fan_speed)

    def set_fan_speed(self, speed: str) -> None:
        """Set AC fan speed immediately."""
        speed_l = str(speed).strip().lower()
        self.ac.set_fan_speed(speed_l)
        self.fan_speed = speed_l
        self._emitter.emit_ac_state(self.mode, self.fan_speed)

    # =========================================================================
    # Thermostat enable/disable
    # =========================================================================

    def enable(self) -> None:
        """Enable thermostat control."""
        self._enabled = True
        self.cfg.thermo_active = True
        self._persist_conf()
        self._emitter.emit_thermostat_status(self._enabled)

    def disable(self) -> None:
        """Disable thermostat control."""
        self._enabled = False
        self.cfg.thermo_active = False
        self._persist_conf()
        self._emitter.emit_thermostat_status(self._enabled)

    # =========================================================================
    # Sleep mode
    # =========================================================================

    def set_sleep_enabled(self, enabled: bool) -> None:
        """Set sleep mode enabled state."""
        self._sleep.set_enabled(enabled)
        self._persist_conf()
        self._emit_sleep_status()
        self.step_sleep_check()

    def set_early_sleep_enabled(self, enabled: bool) -> None:
        """Set sleep mode enabled state."""
        self._sleep.early_sleep_enabled = enabled
        self._emit_sleep_status()
        self.step_sleep_check()

    def set_sleep_times(self, start: str | None, stop: str | None) -> None:
        """Set single sleep start/stop times."""
        self._sleep.set_times(start, stop)
        self._persist_conf()
        self._emit_sleep_status()
        self.step_sleep_check()

    def set_sleep_schedule(self, schedule: dict[str, dict[str, str | None]]) -> None:
        """Set weekly sleep schedule."""
        self._sleep.set_schedule(schedule)
        self._persist_conf()
        self._emit_sleep_status()
        self.step_sleep_check()

    def disable_sleep_for(self, minutes: int) -> None:
        """Temporarily disable sleep enforcement."""
        self._sleep.disable_for(minutes)
        self._emit_sleep_status()
        self.step_sleep_check()

    def sleep_for(self, minutes: int) -> None:
        """Temporarily enforce sleep."""
        logger.debug("thermo: sleep_for requested minutes=%s", minutes)
        self._sleep.sleep_for(minutes)
        self._emit_sleep_status()
        self.step_sleep_check()

    def cancel_sleep_override(self) -> None:
        """Cancel the active temporary sleep override."""
        logger.debug("thermo: cancel_sleep_override requested")
        self._sleep.cancel_sleep_override()
        self._emit_sleep_status()
        self.step_sleep_check()

    def cancel_sleep_for(self) -> None:
        """Cancel the active temporary sleep-for override."""
        logger.debug("thermo: cancel_sleep_for requested")
        self._sleep.cancel_sleep_for()
        self._emit_sleep_status()
        self.step_sleep_check()

    def _emit_sleep_status(self) -> None:
        """Emit sleep status notification."""
        self._emitter.emit_sleep_status(self._sleep.get_status_payload())

    @property
    def is_sleep_window_now(self) -> bool:
        """Return whether current time is within sleep window."""
        return self._sleep.is_sleep_window_now()

    # =========================================================================
    # Temperature control locations
    # =========================================================================

    def set_control_locations(self, locs: list[str]) -> None:
        """Set control locations for temperature reading."""
        self._temp_reader.set_control_locations(locs)
        self._persist_conf()
        self._emitter.emit_config()

    # =========================================================================
    # Thermostat parameters
    # =========================================================================

    def set_setpoint(self, celsius: float) -> None:
        """Set target temperature."""
        try:
            self.cfg.target_temp = float(celsius)
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    def set_hysteresis_split(self, pos_h: float, neg_h: float) -> None:
        """Set positive and negative hysteresis separately."""
        try:
            p = float(pos_h)
            n = float(neg_h)
            if p < 0 or n < 0:
                return
            self.cfg.pos_hysteresis = p
            self.cfg.neg_hysteresis = n
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    def set_hysteresis(self, deadband: float) -> None:
        """Set symmetric hysteresis deadband."""
        try:
            d = float(deadband)
            if d < 0:
                return
        except Exception:
            return
        split = d / 2.0
        self.set_hysteresis_split(split, split)

    def set_min_on_s(self, seconds: int) -> None:
        """Set minimum ON time in seconds."""
        try:
            v = int(seconds)
            if v < 0:
                return
            self.cfg.min_on_s = v
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    def set_min_off_s(self, seconds: int) -> None:
        """Set minimum OFF time in seconds."""
        try:
            v = int(seconds)
            if v < 0:
                return
            self.cfg.min_off_s = v
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    def set_poll_interval_s(self, seconds: int) -> None:
        """Set control loop poll interval."""
        try:
            v = int(seconds)
            if v <= 0:
                return
            self.cfg.poll_interval_s = v
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    def set_smooth_window(self, n: int) -> None:
        """Set temperature smoothing window size."""
        self._temp_reader.set_smooth_window(n)
        self._persist_conf()
        self._emitter.emit_config()

    def set_max_stale_s(self, seconds: int | None) -> None:
        """Set max staleness for temperature readings."""
        try:
            if seconds is None:
                self.cfg.max_stale_s = None
            else:
                v = int(seconds)
                self.cfg.max_stale_s = None if v < 0 else v
        except Exception:
            return
        self._persist_conf()
        self._emitter.emit_config()

    # =========================================================================
    # Control loop
    # =========================================================================

    def step_sleep_check(self) -> bool:
        """Check sleep window and handle transitions. Returns True to continue control."""
        logger.debug(
            "thermo: step_sleep_check: sleep_active=%s is_sleep_time=%s is_on=%s",
            getattr(self.cfg, "sleep_active", True),
            self._sleep.is_sleep_window_now(),
            self._is_on,
        )

        new_sleep = self._sleep.is_sleep_window_now()
        if new_sleep != self._sleep.is_sleep_time:
            s = "ENTERING" if new_sleep else "EXITING"
            logger.info("thermo: %s sleep time window", s)
            self._sleep.is_sleep_time = new_sleep
            self._emit_sleep_status()

        if new_sleep:
            if self._is_on:
                if self._can_turn_off():
                    logger.info("thermo: sleep active — turning OFF")
                    self.turn_off(source="sleep")
                    self._last_change_ts = self._now()
                    self._emit_power_status()
                else:
                    wait = self.cfg.min_on_s - (self._now() - self._last_change_ts)
                    logger.debug(
                        "thermo: sleep active — waiting min-on %.0fs before OFF",
                        max(0, wait),
                    )
            else:
                logger.debug("thermo: sleep active — staying OFF")
            logger.debug("thermo: sleeping %ss (sleep mode)", self.cfg.poll_interval_s)
            time.sleep(self.cfg.poll_interval_s)
            return False
        return True

    def step_on_off_check(self) -> None:
        """Main temperature control logic."""
        temp = self._temp_reader.read_temperature()
        if temp is None:
            logger.warning("thermo: no valid temp (missing or stale); skipping")
            time.sleep(self.cfg.poll_interval_s)
            return

        on_at, off_at = self._thresholds()
        logger.debug(
            "thermo: setpoint=%.2f deadband=%.2f on_at=%.2f off_at=%.2f",
            self.cfg.target_temp,
            self.cfg.pos_hysteresis + self.cfg.neg_hysteresis,
            on_at,
            off_at,
        )
        now = self._now()

        if not self._is_on:
            # OFF -> consider ON
            if temp >= on_at and self._can_turn_on():
                time_delta = self.turn_on()
                logger.info(
                    "thermo: ON trigger: temp=%.2f >= %.2f; turned on after %s min",
                    temp,
                    on_at,
                    time_delta,
                )
                if time_delta:
                    self.ctrl.log_message(
                        f"AC ON, delta={time_delta} min, on_at={on_at}, off_at={off_at}",
                        log_type="ac",
                    )
                try:
                    self.ac.set_temperature(16)
                except Exception as e:
                    logger.debug("thermo: failed to set device temp to 16: %s", e)
                self._last_change_ts = now
                self._emit_power_status()
                logger.debug("thermo: state changed -> ON; temp_set=16")
            else:
                reasons = []
                if temp < on_at:
                    reasons.append(f"temp {temp:.2f} < on_at {on_at:.2f}")
                wait = self.cfg.min_off_s - (now - self._last_change_ts)
                if wait > 0:
                    reasons.append(f"min-off {wait:.0f}s")
                if reasons:
                    logger.debug("thermo: staying OFF: %s", ", ".join(reasons))
        else:
            # ON -> consider OFF
            if temp <= off_at and self._can_turn_off():
                time_delta = self.turn_off()
                logger.info(
                    "thermo: OFF trigger: temp=%.2f <= %.2f; turned off after %s min",
                    temp,
                    off_at,
                    time_delta,
                )
                if time_delta:
                    self.ctrl.log_message(
                        f"AC OFF, delta={time_delta} min, on_at={on_at}, off_at={off_at}",
                        log_type="ac",
                    )
                self._last_change_ts = now
                self._emit_power_status()
                logger.debug("thermo: state changed -> OFF")
            else:
                reasons = []
                if temp > off_at:
                    reasons.append(f"temp {temp:.2f} > off_at {off_at:.2f}")
                wait = self.cfg.min_on_s - (now - self._last_change_ts)
                if wait > 0:
                    reasons.append(f"min-on {wait:.0f}s")
                if reasons:
                    logger.debug("thermo: staying ON: %s", ", ".join(reasons))

    def step(self) -> None:
        """One control step using external temperature."""
        # Refresh actual device state and inform listeners if changed
        try:
            active_correlation_id = self._local_power_correlation_id
            status, diagnostics = self._read_device_status(active_correlation_id)
            if isinstance(status, dict) and "switch" in status:
                now = self._now()
                raw_switch = status["switch"]
                new_is_on = bool(raw_switch)
                if new_is_on != self._is_on:
                    if self._local_power_expected_on is not None:
                        self._local_power_mismatch_count += 1
                    if self._should_ignore_external_state(new_is_on, now):
                        level = (
                            logging.WARNING
                            if self._local_power_mismatch_count == 1
                            else logging.DEBUG
                        )
                        self._log_device_power_observation(
                            level=level,
                            action="suppressed_contradiction",
                            raw_switch=raw_switch,
                            diagnostics=diagnostics,
                            now=now,
                            correlation_id=active_correlation_id,
                        )
                        logger.debug(
                            "thermo: suppressed external state reconciliation "
                            "reported=%s internal=%s",
                            "ON" if new_is_on else "OFF",
                            "ON" if self._is_on else "OFF",
                        )
                    else:
                        correlation_id = active_correlation_id or self._new_correlation_id()
                        self._log_device_power_observation(
                            level=(
                                logging.WARNING
                                if active_correlation_id is not None
                                else logging.INFO
                            ),
                            action=(
                                "accepted_after_settle"
                                if active_correlation_id is not None
                                else "accepted_external_change"
                            ),
                            raw_switch=raw_switch,
                            diagnostics=diagnostics,
                            now=now,
                            correlation_id=correlation_id,
                        )
                        logger.info(
                            "thermo: device state changed externally correlation_id=%s -> %s",
                            correlation_id,
                            "ON" if new_is_on else "OFF",
                        )
                        self._record_external_state(
                            new_is_on,
                            correlation_id=correlation_id,
                        )
                        self._clear_local_power_command("external_state_accepted")
                else:
                    if (
                        self._local_power_expected_on is not None
                        and new_is_on == self._local_power_expected_on
                    ):
                        self._log_device_power_observation(
                            level=logging.INFO,
                            action="command_confirmed",
                            raw_switch=raw_switch,
                            diagnostics=diagnostics,
                            now=now,
                            correlation_id=active_correlation_id,
                        )
                    self._clear_local_power_command("device_matches_internal")
            # Track mode/fan changes
            if isinstance(status, dict):
                changed = False
                m = status.get("mode")
                f = status.get("fan_speed_enum")
                if m is not None and m != self.mode:
                    self.mode = m
                    changed = True
                if f is not None and f != self.fan_speed:
                    self.fan_speed = f
                    changed = True
                if changed:
                    self._emitter.emit_ac_state(self.mode, self.fan_speed)
        except Exception as e:
            logger.debug("thermo: get_status failed at step start: %s", e)

        # If thermostat is disabled, skip control
        if not self._enabled:
            logger.debug("thermo: disabled, skipping control")
            time.sleep(self.cfg.poll_interval_s)
            return

        # Sleep mode check
        resume = self.step_sleep_check()
        if resume:
            self.step_on_off_check()

        logger.debug("thermo: sleeping %ss", self.cfg.poll_interval_s)
        time.sleep(self.cfg.poll_interval_s)

    def run_forever(self) -> None:
        """Run thermostat control loop indefinitely."""
        logger.info("thermo: starting thermostat loop (external temp source)")
        while True:
            try:
                self.step()
            except Exception as e:
                logger.exception("thermo: error during control loop: %s", e)
                time.sleep(self.cfg.poll_interval_s)

    @property
    def is_on(self) -> bool:
        """Current AC power state."""
        return bool(self._is_on)
