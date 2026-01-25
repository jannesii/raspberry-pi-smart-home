"""
AC Thermostat - Main orchestrator.

The main ACThermostat class that coordinates all thermostat activities.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, TYPE_CHECKING

import pytz

from .time_utils import parse_iso_to_epoch, compute_phase_duration
from .sleep_manager import SleepManager
from .temp_reader import TemperatureReader
from .notifier import NotificationEmitter

if TYPE_CHECKING:
    from app.core import Controller, ThermostatConf
    from app.services.ac import ACController

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ACThermostat:
    """Main thermostat controller using external temperature source."""

    def __init__(
        self,
        ac: "ACController",
        cfg: "ThermostatConf",
        ctrl: "Controller",
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
        self.tz = pytz.timezone('Europe/Helsinki')

        # Initialize AC state
        ac_status = self.ac.get_status() if not winter else None
        self._is_on: bool = bool(ac_status.get(
            "switch", False)) if ac_status else False
        self.mode: str | None = ac_status.get(
            "mode", "cold") if isinstance(ac_status, dict) else None
        self.fan_speed: str | None = ac_status.get(
            "fan_speed_enum", "low") if isinstance(ac_status, dict) else None
        self._enabled: bool = bool(getattr(cfg, 'thermo_active', True))
        self._last_change_ts: float = 0.0

        # Track persisted start ISO for the current phase
        self._phase_started_at_iso: str | None = getattr(
            cfg, 'phase_started_at', None)

        # Initialize submodules
        self._sleep = SleepManager(cfg, self.tz)
        self._sleep.is_sleep_time = self._sleep.is_sleep_window_now()
        self._temp_reader = TemperatureReader(ctrl, cfg, location, self.tz)
        self._emitter = NotificationEmitter(notify, cfg)

        # Initialize phase timing
        self._init_phase_timing()

        logger.debug(
            "thermo: init %s is_on=%s mode=%s fan=%s",
            cfg, self._is_on, self.mode, self.fan_speed,
        )

    def _init_phase_timing(self) -> None:
        """Initialize phase timing from persisted state."""
        now_epoch = time.time()
        started_epoch = parse_iso_to_epoch(self._phase_started_at_iso, self.tz)
        logger.debug(
            "thermo: parsed phase_started_at=%s -> %s",
            self._phase_started_at_iso, started_epoch,
        )

        # Validate persisted phase matches actual state
        if self._is_on:
            if getattr(self.cfg, 'current_phase', None) != 'on' or started_epoch is None:
                self._phase_started_at_iso = datetime.fromtimestamp(
                    now_epoch, tz=self.tz).isoformat()
                self._persist_conf()
        else:
            if getattr(self.cfg, 'current_phase', None) != 'off' or started_epoch is None:
                self._phase_started_at_iso = datetime.fromtimestamp(
                    now_epoch, tz=self.tz).isoformat()
                self._persist_conf()

        # Set last-change timestamp from phase start
        started_epoch = parse_iso_to_epoch(self._phase_started_at_iso, self.tz)
        if started_epoch is not None:
            self._last_change_ts = min(now_epoch, float(started_epoch))
        else:
            self._last_change_ts = now_epoch

        # Log phase age
        phase_lbl = 'ON' if self._is_on else 'OFF'
        age_min = compute_phase_duration(
            self._phase_started_at_iso, self.tz) or 0
        logger.info(
            "thermo: current phase=%s age=%d min since %s",
            phase_lbl, age_min, self._phase_started_at_iso,
        )

    def _now(self) -> float:
        return time.time()

    def _can_turn_on(self) -> bool:
        ok = (self._now(
        ) - self._last_change_ts) >= self.cfg.min_off_s and not self._sleep.is_sleep_window_now()
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
                sleep_weekly=getattr(self.cfg, 'sleep_weekly', None),
                control_locations=getattr(self.cfg, 'control_locations', None),
                target_temp=self.cfg.target_temp,
                pos_hysteresis=self.cfg.pos_hysteresis,
                neg_hysteresis=self.cfg.neg_hysteresis,
                thermo_active=self._enabled,
                min_on_s=int(self.cfg.min_on_s),
                min_off_s=int(self.cfg.min_off_s),
                poll_interval_s=int(self.cfg.poll_interval_s),
                smooth_window=int(self.cfg.smooth_window),
                max_stale_s=self.cfg.max_stale_s,
                current_phase=('on' if self._is_on else 'off'),
                phase_started_at=self._phase_started_at_iso,
            )
        except Exception as e:
            logger.debug("thermo: persist conf failed: %s", e)

    def _record_transition(self) -> int | None:
        """Record phase transition, persist, return previous phase duration in minutes."""
        minutes = compute_phase_duration(self._phase_started_at_iso, self.tz)

        # Log AC on/off event into DB
        try:
            self.ctrl.record_ac_event(is_on=bool(
                self._is_on), source='thermostat')
        except Exception as e:
            logger.debug("thermo: failed to record ac_event: %s", e)

        # Set new phase start
        self._phase_started_at_iso = datetime.now(self.tz).isoformat()
        self._persist_conf()
        return minutes

    def _record_external_state(self, new_on: bool) -> None:
        """Update counters on external device state changes without issuing commands."""
        if new_on == self._is_on:
            return
        self._is_on = new_on
        self._record_transition()
        self._last_change_ts = self._now()
        self._emitter.emit_status(self._is_on)

    def _thresholds(self) -> tuple[float, float]:
        """Return (on_at, off_at) temperature thresholds."""
        on_at = self.cfg.target_temp + float(self.cfg.pos_hysteresis)
        off_at = self.cfg.target_temp - float(self.cfg.neg_hysteresis)
        return on_at, off_at

    # =========================================================================
    # Power control
    # =========================================================================

    def turn_on(self) -> int | None:
        """Turn AC on and record transition."""
        self.ac.turn_on()
        self._is_on = True
        return self._record_transition()

    def turn_off(self) -> int | None:
        """Turn AC off and record transition."""
        self.ac.turn_off()
        self._is_on = False
        return self._record_transition()

    def set_power(self, on: bool) -> None:
        """Manually set AC power state."""
        if on:
            self.turn_on()
        else:
            self.turn_off()
        self._last_change_ts = self._now()
        self._emitter.emit_status(self._is_on)

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

    def _emit_sleep_status(self) -> None:
        """Emit sleep status notification."""
        self._emitter.emit_sleep_status(self._sleep.get_status_payload())

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
            getattr(self.cfg, 'sleep_active', True),
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
                    self.turn_off()
                    self._last_change_ts = self._now()
                    self._emitter.emit_status(self._is_on)
                else:
                    wait = self.cfg.min_on_s - \
                        (self._now() - self._last_change_ts)
                    logger.debug(
                        "thermo: sleep active — waiting min-on %.0fs before OFF",
                        max(0, wait),
                    )
            else:
                logger.debug("thermo: sleep active — staying OFF")
            logger.debug("thermo: sleeping %ss (sleep mode)",
                         self.cfg.poll_interval_s)
            time.sleep(self.cfg.poll_interval_s)
            return False
        return True

    def step_on_off_check(self) -> None:
        """Main temperature control logic."""
        temp = self._temp_reader.read_temperature()
        if temp is None:
            logger.warning(
                "thermo: no valid temp (missing or stale); skipping")
            time.sleep(self.cfg.poll_interval_s)
            return

        on_at, off_at = self._thresholds()
        logger.debug(
            "thermo: setpoint=%.2f deadband=%.2f on_at=%.2f off_at=%.2f",
            self.cfg.target_temp,
            self.cfg.pos_hysteresis + self.cfg.neg_hysteresis,
            on_at, off_at,
        )
        now = self._now()

        if not self._is_on:
            # OFF -> consider ON
            if temp >= on_at and self._can_turn_on():
                time_delta = self.turn_on()
                logger.info(
                    "thermo: ON trigger: temp=%.2f >= %.2f; turned on after %s min",
                    temp, on_at, time_delta,
                )
                if time_delta:
                    self.ctrl.log_message(
                        f"AC ON, delta={time_delta} min, on_at={on_at}, off_at={off_at}",
                        log_type="ac",
                    )
                try:
                    self.ac.set_temperature(16)
                except Exception as e:
                    logger.debug(
                        "thermo: failed to set device temp to 16: %s", e)
                self._last_change_ts = now
                self._emitter.emit_status(self._is_on)
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
                    temp, off_at, time_delta,
                )
                if time_delta:
                    self.ctrl.log_message(
                        f"AC OFF, delta={time_delta} min, on_at={on_at}, off_at={off_at}",
                        log_type="ac",
                    )
                self._last_change_ts = now
                self._emitter.emit_status(self._is_on)
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
            status = self.ac.get_status()
            if isinstance(status, dict) and 'switch' in status:
                new_is_on = bool(status.get('switch', False))
                if new_is_on != self._is_on:
                    logger.info(
                        "thermo: device state changed externally -> %s",
                        "ON" if new_is_on else "OFF",
                    )
                    self._record_external_state(new_is_on)
            # Track mode/fan changes
            if isinstance(status, dict):
                changed = False
                m = status.get('mode')
                f = status.get('fan_speed_enum')
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
