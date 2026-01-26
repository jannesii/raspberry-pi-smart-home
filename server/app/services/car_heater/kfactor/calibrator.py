"""
KFactor Calibrator - Main orchestrator.

The main KFactorCalibrator class that coordinates all calibration activities.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .constants import (
    bucket_key,
    dt_from_any,
    finite,
    iso_no_micros,
    parse_hhmm,
)
from .models import KFactorConfig
from .physics import ThermalPhysics
from .session import SessionManager
from .snapshot import SnapshotGenerator

if TYPE_CHECKING:
    from app.core import Controller
    from app.services.car_heater import (
        CarHeaterService,
        ChargeModeState,
        KeepAtTempService,
        ReadyBySchedule,
        ReadyByService,
    )
    from app.services.weather import WeatherData, WeatherService

logger = logging.getLogger(__name__)


# ==========================================================================
# STATES
# ==========================================================================

STATE_IDLE = "IDLE"
STATE_PASSIVE_RECORDING = "PASSIVE_RECORDING"
STATE_AUTONOMOUS_ARMED = "AUTONOMOUS_ARMED"
STATE_AUTONOMOUS_RECORDING = "AUTONOMOUS_RECORDING"
STATE_COOLDOWN = "COOLDOWN"


class KFactorCalibrator:
    """
    Calibrates thermal parameters (k_loss, eta) for cabin heating prediction.

    Can operate in two modes:
    - Passive: Records sessions when the heater is on from other sources
    - Autonomous: Actively turns heater on/off for calibration when conditions allow
    """

    def __init__(
        self,
        ctrl: Controller | None = None,
        *,
        tz: ZoneInfo | None = None,
        is_test: bool = False,
    ) -> None:
        self._ctrl = ctrl
        self._tz = tz or ZoneInfo("Europe/Helsinki")
        self._is_test = is_test

        # Load or create config
        self._cfg = self._load_config_from_db() or KFactorConfig()

        # State
        self._state: str = STATE_IDLE
        self._slow_rise_checks: int = 0
        self._cooldown_until: datetime | None = None
        self._last_heater_state: bool = False

        # Submodules
        self._physics = ThermalPhysics(self._cfg)
        self._session = SessionManager(
            cfg=self._cfg,
            ctrl=ctrl,
            physics=self._physics,
            tz=self._tz,
            get_active_params_fn=self.get_active_params,
        )
        self._snapshot = SnapshotGenerator(cfg=self._cfg, tz=self._tz)

        # Service references (set externally)
        self._weather_service: WeatherService | None = None
        self._car_heater_service: CarHeaterService | None = None
        self._ready_by_service: ReadyByService | None = None
        self._keep_at_temp_service: KeepAtTempService | None = None

        # Load cooldown from DB (survives reboots)
        self._load_cooldown_from_db()

        logger.info(
            "kfactor: initialized (state=%s, enabled=%s, test=%s)",
            self._state,
            self._cfg.enabled,
            is_test,
        )

    # ==========================================================================
    # PROPERTIES
    # ==========================================================================

    @property
    def state(self) -> str:
        return self._state

    @property
    def config(self) -> KFactorConfig:
        return self._cfg

    @property
    def enabled(self) -> bool:
        """Whether autonomous calibration is enabled."""
        return self._cfg.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Set autonomous calibration enabled state."""
        self._cfg = replace(self._cfg, enabled=bool(value))
        self._save_config_in_db(self._cfg)
        logger.info("kfactor: enabled=%s", value)

    # ==========================================================================
    # SERVICE REFERENCES
    # ==========================================================================

    def set_weather_service(self, svc: WeatherService | None) -> None:
        self._weather_service = svc

    def set_car_heater_service(self, svc: CarHeaterService | None) -> None:
        self._car_heater_service = svc

    def set_ready_by_service(self, svc: Any) -> None:
        self._ready_by_service = svc

    def set_keep_at_temp_service(self, svc: KeepAtTempService | None) -> None:
        self._keep_at_temp_service = svc

    # ==========================================================================
    # CONFIG MANAGEMENT
    # ==========================================================================

    def update_config(self, updates: dict[str, Any]) -> None:
        """Merge user config updates into current config."""
        self._cfg = self._merge_config(updates)
        self._save_config_in_db(self._cfg)
        # Update submodules with new config
        self._physics = ThermalPhysics(self._cfg)
        self._session._cfg = self._cfg
        self._snapshot._cfg = self._cfg
        logger.info("kfactor: config updated %s", updates)

    def _load_config_from_db(self) -> KFactorConfig | None:
        """Load config from database."""
        logger.debug("kfactor: _load_config_from_db called")
        if self._ctrl is None:
            logger.debug("kfactor: _load_config_from_db skipped (no ctrl)")
            return None
        try:
            row = self._ctrl.get_kfactor_config()
            logger.debug("kfactor: raw kfactor config row=%s", row)
            if row is None:
                return None
            config_json = None
            if isinstance(row, dict):
                config_json = row.get("config_json")
            else:
                config_json = getattr(row, "config_json", None)
            if not config_json:
                logger.debug("kfactor: no config_json found in DB row")
                return None
            import json
            from dataclasses import fields

            raw = json.loads(config_json)
            allowed = {f.name for f in fields(KFactorConfig)}
            filtered = {key: value for key, value in raw.items() if key in allowed}
            logger.debug("kfactor: loaded config keys=%s", sorted(filtered.keys()))
            return KFactorConfig(**filtered)
        except Exception:
            logger.debug("kfactor: failed to load config from DB", exc_info=True)
            return None

    def _save_config_in_db(self, cfg: KFactorConfig) -> None:
        """Save config to database."""
        logger.debug("kfactor: _save_config_in_db called with cfg=%s", cfg)
        if self._ctrl is None:
            logger.debug("kfactor: _save_config_in_db skipped (no ctrl)")
            return
        try:
            import json
            from dataclasses import asdict

            from app.core.models import CarHeaterKFactorConfig

            payload = json.dumps(asdict(cfg), separators=(",", ":"), sort_keys=True)
            now = iso_no_micros(datetime.now(tz=self._tz))
            self._ctrl.save_kfactor_config(
                CarHeaterKFactorConfig(
                    id=1,
                    config_json=payload,
                    updated_ts=now,
                )
            )
        except Exception:
            logger.debug("kfactor: failed to save config to DB", exc_info=True)

    def _merge_config(self, updates: dict[str, Any]) -> KFactorConfig:
        """Merge updates into current config."""
        from dataclasses import fields

        current = {f.name: getattr(self._cfg, f.name) for f in fields(self._cfg)}
        for key, value in updates.items():
            if key in current:
                current[key] = value
        return KFactorConfig(**current)

    # ==========================================================================
    # COOLDOWN MANAGEMENT
    # ==========================================================================

    def _load_cooldown_from_db(self) -> None:
        """Load cooldown timestamp from database (survives reboots)."""
        logger.debug("kfactor: _load_cooldown_from_db called")
        if self._ctrl is None:
            logger.debug("kfactor: _load_cooldown_from_db skipped (no ctrl)")
            return
        try:
            row = self._ctrl.get_kfactor_cooldown()
            logger.debug("kfactor: cooldown row=%s", row)
            if row:
                cooldown_until = None
                if isinstance(row, dict):
                    cooldown_until = row.get("cooldown_until")
                else:
                    cooldown_until = getattr(row, "cooldown_until", None)
            else:
                cooldown_until = None
            if cooldown_until:
                ts = dt_from_any(cooldown_until, self._tz)
                if ts and ts > datetime.now(tz=self._tz):
                    self._cooldown_until = ts
                    logger.info("kfactor: loaded cooldown_until=%s from DB", ts)
        except Exception:
            logger.debug("kfactor: failed to load cooldown from DB", exc_info=True)

    def _save_cooldown_to_db(self, until: datetime | None) -> None:
        """Save cooldown timestamp to database."""
        logger.debug("kfactor: _save_cooldown_to_db called until=%s", until)
        if self._ctrl is None:
            logger.debug("kfactor: _save_cooldown_to_db skipped (no ctrl)")
            return
        try:
            from app.core.models import CarHeaterKFactorCooldown

            payload = iso_no_micros(until) if until else None
            now = iso_no_micros(datetime.now(tz=self._tz))
            self._ctrl.save_kfactor_cooldown(
                CarHeaterKFactorCooldown(
                    id=1,
                    cooldown_until=payload,
                    updated_ts=now,
                )
            )
        except Exception:
            logger.debug("kfactor: failed to save cooldown to DB", exc_info=True)

    def _set_cooldown(self, end_ts: datetime, is_autonomous: bool, reason: str) -> None:
        """Set cooldown period after a session."""
        if is_autonomous:
            minutes = int(self._cfg.autonomous_cooldown_minutes)
        else:
            minutes = int(self._cfg.cooldown_minutes)

        self._cooldown_until = end_ts + timedelta(minutes=minutes)
        self._save_cooldown_to_db(self._cooldown_until)
        logger.debug(
            "kfactor: cooldown set until %s (reason=%s, autonomous=%s)",
            iso_no_micros(self._cooldown_until),
            reason,
            is_autonomous,
        )

    # ==========================================================================
    # ACTIVE PARAMS
    # ==========================================================================

    def get_active_params(self) -> tuple[float, float]:
        """Return active (global) parameters (k_loss, eta)."""
        # Check for test-mode override
        if self._session.active_params_override is not None:
            return self._session.active_params_override

        # Check database
        if self._ctrl is not None:
            try:
                params = self._ctrl.get_kfactor_active_params()
                if params is not None:
                    return (
                        float(params.k_loss_W_per_K),
                        float(params.eta),
                    )
            except Exception:
                logger.debug("kfactor: failed to load active params from DB", exc_info=True)

        # Defaults
        return (
            float(self._cfg.default_k_loss_W_per_K),
            float(self._cfg.default_eta),
        )

    def get_bucket_params(
        self,
        *,
        outside_temp_c: float,
        wind_m_s: float | None,
    ) -> tuple[float, float] | None:
        """Get bucket-specific parameters if available."""
        t_bucket, wind_bucket = bucket_key(outside_temp_c, wind_m_s)

        # Check override
        override = self._session.bucket_params_override.get((t_bucket, wind_bucket))
        if override is not None:
            return override

        # Check database
        if self._ctrl is not None:
            try:
                params = self._ctrl.get_kfactor_bucket_params(
                    t_bucket=t_bucket,
                    wind_bucket=wind_bucket,
                )
                if params is not None:
                    return (
                        float(params.k_loss_W_per_K),
                        float(params.eta),
                    )
            except Exception:
                logger.debug("kfactor: failed to load bucket params from DB", exc_info=True)

        return None

    def should_calibrate(
        self,
        *,
        outside_temp_c: float,
        wind_m_s: float | None,
    ) -> tuple[bool, str]:
        """Check if calibration is needed for current conditions."""
        t_bucket, wind_bucket = bucket_key(outside_temp_c, wind_m_s)

        # Check recent bucket data
        if self._ctrl is not None:
            try:
                lookback = int(self._cfg.bucket_lookback_days)
                recent = self._ctrl.count_kfactor_bucket_samples(
                    t_bucket=t_bucket,
                    wind_bucket=wind_bucket,
                    lookback_days=lookback,
                )
                if recent and recent > 0:
                    return False, f"bucket has {recent} recent samples"
            except Exception:
                logger.debug("kfactor: failed to check bucket samples", exc_info=True)

        return True, "no recent bucket data"

    # ==========================================================================
    # TICK LOOP
    # ==========================================================================

    def tick(
        self,
        *,
        now: datetime | None = None,
        is_heater_on: bool,
        power_w: float,
        cabin_temp_c: float,
    ) -> None:
        """Main tick called periodically with current sensor readings."""
        if now is None:
            now = datetime.now(tz=self._tz)

        # Get weather data
        outside_temp_c, wind_m_s = self._get_weather()
        if outside_temp_c is None:
            logger.debug("kfactor: tick skipped, no outside_temp")
            return

        # Detect heater state transitions
        heater_just_turned_on = is_heater_on and not self._last_heater_state
        heater_just_turned_off = not is_heater_on and self._last_heater_state
        self._last_heater_state = is_heater_on

        # State machine
        if self._cfg.enabled:
            # Autonomous mode
            self._tick_autonomous(
                now=now,
                is_heater_on=is_heater_on,
                power_w=power_w,
                cabin_temp_c=cabin_temp_c,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
                heater_just_turned_on=heater_just_turned_on,
                heater_just_turned_off=heater_just_turned_off,
            )
        else:
            # Passive mode
            self._tick_passive(
                now=now,
                is_heater_on=is_heater_on,
                power_w=power_w,
                cabin_temp_c=cabin_temp_c,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
                heater_just_turned_on=heater_just_turned_on,
                heater_just_turned_off=heater_just_turned_off,
            )

    def _tick_passive(
        self,
        *,
        now: datetime,
        is_heater_on: bool,
        power_w: float,
        cabin_temp_c: float,
        outside_temp_c: float,
        wind_m_s: float | None,
        heater_just_turned_on: bool,
        heater_just_turned_off: bool,
    ) -> None:
        """Passive recording tick."""
        # Check cooldown
        if self._cooldown_until and now < self._cooldown_until:
            if self._state != STATE_COOLDOWN:
                self._state = STATE_COOLDOWN
                logger.debug("kfactor: entering COOLDOWN (until %s)", self._cooldown_until)
            return

        if self._state == STATE_COOLDOWN:
            self._state = STATE_IDLE
            self._cooldown_until = None
            logger.debug("kfactor: cooldown expired, back to IDLE")

        if self._state == STATE_IDLE:
            if heater_just_turned_on:
                self._session.start_session(
                    now=now,
                    window_start=None,
                    window_stop=None,
                    cabin_temp_c=cabin_temp_c,
                    heater_on=is_heater_on,
                    power_w=power_w,
                    outside_temp_c=outside_temp_c,
                    wind_m_s=wind_m_s,
                    is_autonomous=False,
                )
                self._state = STATE_PASSIVE_RECORDING
                logger.debug("kfactor: IDLE -> PASSIVE_RECORDING")

        elif self._state == STATE_PASSIVE_RECORDING:
            # Append sample
            self._session.append_sample(
                ts=now,
                cabin_temp_c=cabin_temp_c,
                heater_on=is_heater_on,
                power_w=power_w,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
            )

            # Check disturbance
            disturbance = self._session.detect_disturbance()
            if disturbance:
                logger.info("kfactor: session aborted due to %s", disturbance)
                self._session.finalize_session(
                    end_ts=now,
                    reason=disturbance,
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check max duration
            duration_s = self._session.session_duration_s(now)
            max_s = int(self._cfg.max_session_minutes) * 60
            if duration_s is not None and duration_s >= max_s:
                logger.info("kfactor: session ended (max_duration)")
                self._session.finalize_session(
                    end_ts=now,
                    reason="max_duration",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check heater off
            if heater_just_turned_off:
                logger.info("kfactor: session ended (heater_off)")
                self._session.finalize_session(
                    end_ts=now,
                    reason="heater_off",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN

    def _tick_autonomous(
        self,
        *,
        now: datetime,
        is_heater_on: bool,
        power_w: float,
        cabin_temp_c: float,
        outside_temp_c: float,
        wind_m_s: float | None,
        heater_just_turned_on: bool,
        heater_just_turned_off: bool,
    ) -> None:
        """Autonomous calibration tick."""
        in_window = self._is_in_window(now)

        # Check cooldown
        if self._cooldown_until and now < self._cooldown_until:
            if self._state not in (STATE_COOLDOWN, STATE_AUTONOMOUS_RECORDING):
                self._state = STATE_COOLDOWN
                logger.debug("kfactor: entering COOLDOWN (until %s)", self._cooldown_until)
            if self._state == STATE_COOLDOWN:
                return

        if self._state == STATE_COOLDOWN:
            self._state = STATE_IDLE
            self._cooldown_until = None
            logger.debug("kfactor: cooldown expired, back to IDLE")

        if self._state == STATE_IDLE:
            if in_window and not is_heater_on:
                # Check for conflicts
                conflict = self._check_autonomous_conflicts()
                if conflict:
                    logger.debug("kfactor: cannot arm, conflict: %s", conflict)
                    return
                self._state = STATE_AUTONOMOUS_ARMED
                logger.info("kfactor: IDLE -> AUTONOMOUS_ARMED (window open)")

        elif self._state == STATE_AUTONOMOUS_ARMED:
            if not in_window:
                self._state = STATE_IDLE
                logger.info("kfactor: AUTONOMOUS_ARMED -> IDLE (window closed)")
                return

            if is_heater_on:
                # Someone else turned heater on, observe passively
                self._session.start_session(
                    now=now,
                    window_start=self._get_window_start(now),
                    window_stop=self._get_window_stop(now),
                    cabin_temp_c=cabin_temp_c,
                    heater_on=is_heater_on,
                    power_w=power_w,
                    outside_temp_c=outside_temp_c,
                    wind_m_s=wind_m_s,
                    is_autonomous=False,
                )
                self._state = STATE_PASSIVE_RECORDING
                logger.info("kfactor: AUTONOMOUS_ARMED -> PASSIVE_RECORDING (external heater)")
                return

            # Check if we should calibrate
            should, reason = self.should_calibrate(
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
            )
            if not should:
                logger.debug("kfactor: skip calibration: %s", reason)
                return

            # Check conflicts again
            conflict = self._check_autonomous_conflicts()
            if conflict:
                logger.debug("kfactor: cannot start, conflict: %s", conflict)
                return

            # Start autonomous session
            logger.info("kfactor: starting autonomous calibration")
            self._turn_heater_on()
            self._session.start_session(
                now=now,
                window_start=self._get_window_start(now),
                window_stop=self._get_window_stop(now),
                cabin_temp_c=cabin_temp_c,
                heater_on=True,
                power_w=power_w,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
                is_autonomous=True,
            )
            self._slow_rise_checks = 0
            self._state = STATE_AUTONOMOUS_RECORDING

        elif self._state == STATE_AUTONOMOUS_RECORDING:
            # Append sample
            self._session.append_sample(
                ts=now,
                cabin_temp_c=cabin_temp_c,
                heater_on=is_heater_on,
                power_w=power_w,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
            )

            # Check disturbance
            disturbance = self._session.detect_disturbance()
            if disturbance:
                logger.info("kfactor: autonomous session aborted: %s", disturbance)
                self._turn_heater_off()
                self._session.finalize_session(
                    end_ts=now,
                    reason=disturbance,
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check target temperature reached
            target_c = float(self._cfg.autonomous_target_temp_c)
            if cabin_temp_c >= target_c:
                logger.info(
                    "kfactor: autonomous session ended (target_reached %.1fC)", cabin_temp_c
                )
                self._turn_heater_off()
                self._session.finalize_session(
                    end_ts=now,
                    reason="target_reached",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check slow rise rate
            slow_rise = self._check_slow_rise_rate()
            if slow_rise:
                logger.info("kfactor: autonomous session aborted (slow_rise)")
                self._turn_heater_off()
                self._session.finalize_session(
                    end_ts=now,
                    reason="slow_rise",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check max duration
            duration_s = self._session.session_duration_s(now)
            max_s = int(self._cfg.autonomous_max_session_minutes) * 60
            if duration_s is not None and duration_s >= max_s:
                logger.info("kfactor: autonomous session ended (max_duration)")
                self._turn_heater_off()
                self._session.finalize_session(
                    end_ts=now,
                    reason="max_duration",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN
                return

            # Check heater unexpectedly off
            if not is_heater_on:
                logger.info("kfactor: autonomous session aborted (heater_off_unexpected)")
                self._session.finalize_session(
                    end_ts=now,
                    reason="heater_off_unexpected",
                    is_test=self._is_test,
                    set_cooldown_fn=self._set_cooldown,
                )
                self._state = STATE_COOLDOWN

        elif self._state == STATE_PASSIVE_RECORDING:
            # Same as passive tick
            self._tick_passive(
                now=now,
                is_heater_on=is_heater_on,
                power_w=power_w,
                cabin_temp_c=cabin_temp_c,
                outside_temp_c=outside_temp_c,
                wind_m_s=wind_m_s,
                heater_just_turned_on=heater_just_turned_on,
                heater_just_turned_off=heater_just_turned_off,
            )

    # ==========================================================================
    # AUTONOMOUS CONTROL
    # ==========================================================================

    def _check_autonomous_conflicts(self) -> str | None:
        """Check for conflicts that prevent autonomous calibration."""

        if self._ready_by_service is not None:
            schedule: ReadyBySchedule | None = self._ready_by_service.get_schedule(as_object=True)
            if schedule is not None:
                status = schedule.status
                if status == "running":
                    logger.debug("kfactor: autonomous blocked (Ready-by running)")
                    return False
                hours_ahead = self._ready_by_service.hours_before_target
                if status == "scheduled" and hours_ahead is not None and hours_ahead < 2.0:
                    logger.debug("kfactor: autonomous blocked (Ready-by scheduled soon)")
                    return False

        if self._keep_at_temp_service is not None and self._keep_at_temp_service.enabled:
            logger.debug("kfactor: autonomous blocked (Keep-at-temp enabled)")
            return False

        if self._car_heater_service is not None:
            charge_mode_state: ChargeModeState | None = (
                self._car_heater_service.get_charge_mode_state()
            )
            if charge_mode_state is not None and charge_mode_state.enabled:
                logger.debug("kfactor: autonomous blocked (Charge mode active)")
                return False

        return True

    def _check_slow_rise_rate(self) -> bool:
        """Check if temperature rise rate is too slow."""
        samples = self._session.samples
        window = int(self._cfg.autonomous_rise_rate_window_samples)
        min_checks = int(self._cfg.autonomous_slow_rise_samples)

        if len(samples) < window + 1:
            return False

        recent = samples[-window:]
        if len(recent) < 2:
            return False

        dt_s = (recent[-1].ts - recent[0].ts).total_seconds()
        if dt_s <= 0:
            return False

        dT = recent[-1].cabin_temp_c - recent[0].cabin_temp_c
        rate_per_min = (dT / dt_s) * 60.0

        min_rate = float(self._cfg.autonomous_min_temp_rise_rate_C_per_min)
        if rate_per_min < min_rate:
            self._slow_rise_checks += 1
            logger.debug(
                "kfactor: slow rise rate %.3f C/min (min=%.3f, checks=%d/%d)",
                rate_per_min,
                min_rate,
                self._slow_rise_checks,
                min_checks,
            )
        else:
            self._slow_rise_checks = 0

        return self._slow_rise_checks >= min_checks

    def _turn_heater_on(self) -> None:
        """Turn heater on for autonomous calibration."""
        if self._car_heater_service is None:
            logger.warning("kfactor: cannot turn heater on, no car_heater_service")
            return
        try:
            self._car_heater_service.turn_on(source="kfactor_calibration")
            logger.info("kfactor: heater turned ON")
        except Exception:
            logger.exception("kfactor: failed to turn heater on")

    def _turn_heater_off(self) -> None:
        """Turn heater off after autonomous calibration."""
        if self._car_heater_service is None:
            logger.warning("kfactor: cannot turn heater off, no car_heater_service")
            return
        try:
            self._car_heater_service.turn_off(source="kfactor_calibration")
            logger.info("kfactor: heater turned OFF")
        except Exception:
            logger.exception("kfactor: failed to turn heater off")

    # ==========================================================================
    # TIME WINDOW
    # ==========================================================================

    def _is_in_window(self, now: datetime) -> bool:
        """Check if now is within the calibration window."""
        start_hhmm = parse_hhmm(self._cfg.auto_calib_start_hhmm)
        stop_hhmm = parse_hhmm(self._cfg.auto_calib_stop_hhmm)

        if start_hhmm is None or stop_hhmm is None:
            return False

        now_hm = (now.hour, now.minute)
        return start_hhmm <= now_hm <= stop_hhmm

    def _get_window_start(self, now: datetime) -> datetime | None:
        """Get start of today's calibration window."""
        start_hhmm = parse_hhmm(self._cfg.auto_calib_start_hhmm)
        if start_hhmm is None:
            return None
        return now.replace(hour=start_hhmm[0], minute=start_hhmm[1], second=0, microsecond=0)

    def _get_window_stop(self, now: datetime) -> datetime | None:
        """Get end of today's calibration window."""
        stop_hhmm = parse_hhmm(self._cfg.auto_calib_stop_hhmm)
        if stop_hhmm is None:
            return None
        return now.replace(hour=stop_hhmm[0], minute=stop_hhmm[1], second=59, microsecond=0)

    # ==========================================================================
    # WEATHER
    # ==========================================================================

    def _get_weather(self) -> tuple[float | None, float | None]:
        """Get current outside temperature and wind speed."""
        if self._weather_service is None:
            logger.debug("kfactor: _get_weather skipped (no weather service)")
            return None, None
        try:
            current: WeatherData = self._weather_service.get_latest()
            if current is None:
                logger.debug("kfactor: no weather data available")
                return None, None
            t2m = getattr(current, "t2m", None)
            ws_10min = getattr(current, "ws_10min", None)
            t2m_val = getattr(t2m, "value", t2m)
            ws_val = getattr(ws_10min, "value", ws_10min)

            outside_c = finite(t2m_val)
            wind = finite(ws_val)

            return outside_c, wind
        except Exception:
            logger.debug("kfactor: failed to get weather", exc_info=True)
            return None, None

    # ==========================================================================
    # SNAPSHOTS
    # ==========================================================================

    def get_debug_snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        """Get debug snapshot."""
        active_k, active_eta = self.get_active_params()
        return self._snapshot.get_debug_snapshot(
            state=self._state,
            active_k=active_k,
            active_eta=active_eta,
            session_started_at=self._session.started_at,
            session_samples=self._session.samples,
            is_autonomous_session=self._session.is_autonomous,
            cooldown_until=self._cooldown_until,
            last_session=self._session.last_session,
            slow_rise_checks=self._slow_rise_checks,
            now=now,
        )

    def get_extended_snapshot(
        self,
        *,
        cabin_temp_c: float | None = None,
        outside_temp_c: float | None = None,
        is_heater_on: bool = False,
        target_temp_c: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Get extended snapshot for UI."""
        active_k, active_eta = self.get_active_params()
        return self._snapshot.get_extended_snapshot(
            state=self._state,
            active_k=active_k,
            active_eta=active_eta,
            session_started_at=self._session.started_at,
            session_samples=self._session.samples,
            is_autonomous_session=self._session.is_autonomous,
            cooldown_until=self._cooldown_until,
            last_session=self._session.last_session,
            slow_rise_checks=self._slow_rise_checks,
            physics=self._physics,
            cabin_temp_c=cabin_temp_c,
            outside_temp_c=outside_temp_c,
            is_heater_on=is_heater_on,
            target_temp_c=target_temp_c,
            now=now,
        )

    # ==========================================================================
    # PREDICTION
    # ==========================================================================

    def predict_time_to_target_minutes(
        self,
        *,
        cabin_temp_c: float,
        target_temp_c: float,
        outside_temp_c: float,
        power_w: float | None = None,
    ) -> float | None:
        """Predict time to reach target temperature."""
        active_k, active_eta = self.get_active_params()
        return self._physics.predict_time_to_target_minutes(
            cabin_temp_c=cabin_temp_c,
            target_temp_c=target_temp_c,
            outside_temp_c=outside_temp_c,
            k_loss=active_k,
            eta=active_eta,
            power_w=power_w,
        )

    def record_prediction_outcome(
        self,
        *,
        predicted_minutes: float,
        actual_minutes: float,
        cabin_start_c: float,
        cabin_end_c: float,
        target_c: float,
        outside_c: float,
    ) -> None:
        """Record prediction outcome for analysis."""
        error_minutes = actual_minutes - predicted_minutes
        error_pct = (error_minutes / predicted_minutes) * 100.0 if predicted_minutes > 0 else 0.0

        logger.info(
            "kfactor: prediction outcome: predicted=%.1fmin actual=%.1fmin error=%.1fmin (%.1f%%) "
            "cabin=%.1f->%.1fC target=%.1fC outside=%.1fC",
            predicted_minutes,
            actual_minutes,
            error_minutes,
            error_pct,
            cabin_start_c,
            cabin_end_c,
            target_c,
            outside_c,
        )

        if self._ctrl is not None:
            try:
                self._ctrl.record_kfactor_prediction_outcome(
                    predicted_minutes=predicted_minutes,
                    actual_minutes=actual_minutes,
                    error_minutes=error_minutes,
                    cabin_start_c=cabin_start_c,
                    cabin_end_c=cabin_end_c,
                    target_c=target_c,
                    outside_c=outside_c,
                )
            except Exception:
                logger.debug("kfactor: failed to record prediction outcome", exc_info=True)

    # ==========================================================================
    # RESET
    # ==========================================================================

    def reset(self, reason: str = "manual_reset") -> None:
        """Reset calibrator state."""
        self._session.reset(reason)
        self._state = STATE_IDLE
        self._slow_rise_checks = 0
        self._cooldown_until = None
        self._save_cooldown_to_db(None)
        logger.info("kfactor: reset (reason=%s)", reason)
