from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import fields
from datetime import datetime, timedelta
import logging
import math
import json
import os
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from app.services.car_heater.keep_at_temp_service import KeepAtTempService

from ...core import CarHeaterReadyByState, CarHeaterStatus, Controller
from ..weather.weather_service import WeatherService
from .car_heater_service import CarHeaterService
from .kfactor_calibrator import KFactorCalibrator

logger = logging.getLogger(__name__)


def _finite(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def _dt_local(ts: Any, tz: ZoneInfo) -> datetime | None:
    if isinstance(ts, datetime):
        return ts.astimezone(tz) if ts.tzinfo else ts.replace(tzinfo=tz)
    if isinstance(ts, str) and ts.strip():
        try:
            dt = datetime.fromisoformat(ts.strip())
            return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            return None
    return None


@dataclass
class ReadyBySchedule:
    created_ts: str
    ready_by_ts: str
    target_temp_c: float
    status: str = "scheduled"  # scheduled|running|completed|canceled|expired|error
    cancel_reason: str | None = None
    last_eval_ts: str | None = None
    planned_start_ts: str | None = None
    predicted_eta_minutes: float | None = None
    outside_temp_c: float | None = None
    cabin_temp_c: float | None = None
    unreachable: bool = False
    last_command_ts: str | None = None
    last_command: str | None = None
    # Outcome tracking
    reached_ts: str | None = None
    reached_offset_min: float | None = None
    reached_cabin_temp_c: float | None = None
    reached_outside_temp_c: float | None = None
    reached_too_early: bool = False
    reached_too_late: bool = False
    used_k_loss_W_per_K: float | None = None
    used_eta: float | None = None


@dataclass
class ReadyByConfig:
    enabled: bool = True
    command_cooldown_s: int = 30
    reach_tolerance_minutes: float = 2.0


class ReadyByService:
    """Schedules and executes "Ready-by" behavior using the calibrated heating model.

    This service is designed to be ticked from the car heater status stream and
    issues turn_on/turn_off commands via CarHeaterService when appropriate.
    """

    def __init__(
        self,
        *,
        car_heater_service: CarHeaterService,
        kfactor_calibrator: KFactorCalibrator,
        keep_at_temp_service: KeepAtTempService,
        ctrl: Controller | None = None,
        weather_service: WeatherService | None = None,
        config: ReadyByConfig | None = None,
        tz_name: str = "Europe/Helsinki",
    ) -> None:
        self._lock = RLock()
        self._tz = ZoneInfo(tz_name)
        self._car_heater_service = car_heater_service
        self._kfactor = kfactor_calibrator
        self._keep_at_temp_service = keep_at_temp_service
        self._ctrl = ctrl
        self._weather = weather_service
        self._cfg = config or self._load_config_from_env()

        self._schedule: ReadyBySchedule | None = None
        self._last_persisted_json: str | None = None
        
        self._restore_from_db()
        
    @property
    def is_enabled(self) -> bool:
        return self._cfg.enabled
    
    @is_enabled.setter
    def is_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._cfg.enabled = enabled
            logger.info("ReadyByService enabled set to: %r", enabled)

    def schedule(
        self,
        *,
        ready_by_ts: datetime,
        target_temp_c: float,
    ) -> ReadyBySchedule:
        """Schedule a Ready-by run (timestamps interpreted in local timezone if naive)."""
        with self._lock:
            dt = ready_by_ts.astimezone(self._tz) if ready_by_ts.tzinfo else ready_by_ts.replace(tzinfo=self._tz)
            now = datetime.now(self._tz).replace(microsecond=0)
            self._schedule = ReadyBySchedule(
                created_ts=now.isoformat(sep=" "),
                ready_by_ts=dt.replace(microsecond=0).isoformat(sep=" "),
                target_temp_c=float(target_temp_c),
            )
            logger.info("ready_by: scheduled for %s target=%.1fC", self._schedule.ready_by_ts, target_temp_c)
            self._persist_if_changed(is_test=False)
            return ReadyBySchedule(**asdict(self._schedule))

    def cancel(self, *, reason: str = "user", turn_off: bool = False) -> ReadyBySchedule | None:
        """Cancel the active schedule. Optionally queue a turn_off."""
        with self._lock:
            if self._schedule is None:
                return None
            self._schedule.status = "canceled"
            self._schedule.cancel_reason = reason
            if turn_off:
                self._queue_command("turn_off")
            logger.info("ready_by: canceled (%s)", reason)
            self._persist_if_changed(is_test=False)
            return ReadyBySchedule(**asdict(self._schedule))

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {"schedule": asdict(self._schedule) if self._schedule else None}

    def tick(
        self,
        car_status: CarHeaterStatus,
        *,
        outside_temp_c: float | None = None,
        is_test: bool = False,
    ) -> None:
        """Evaluate schedule and queue commands as needed."""
        if not self._cfg.enabled:
            return
        now = _dt_local(getattr(car_status, "timestamp", None), self._tz)
        if now is None:
            return

        with self._lock:
            if self._schedule is None:
                return
            s = self._schedule
            if s.status in ("canceled", "expired", "completed"):
                return

            ready_by_dt = _dt_local(s.ready_by_ts, self._tz)
            if ready_by_dt is None:
                s.status = "error"
                s.cancel_reason = "invalid_ready_by_ts"
                return

            if now >= (ready_by_dt + timedelta(minutes=5)):
                s.status = "expired"
                self._persist_if_changed(is_test=is_test)
                return

            cabin_temp_c = _finite(getattr(car_status, "ambient_temp", None))
            if cabin_temp_c is None:
                return

            out = _finite(outside_temp_c)
            if out is None:
                try:
                    if self._weather is not None:
                        w = self._weather.get_latest()
                        if w.t2m is not None:
                            out = _finite(w.t2m.value)
                except Exception:
                    out = None
            if out is None:
                return

            s.last_eval_ts = now.replace(microsecond=0).isoformat(sep=" ")
            s.cabin_temp_c = cabin_temp_c
            s.outside_temp_c = out

            # Predict ETA; if unreachable, start ASAP to best-effort warm.
            used_k, used_eta = self._kfactor.get_active_params()
            s.used_k_loss_W_per_K = float(used_k)
            s.used_eta = float(used_eta)
            eta_min = self._kfactor.predict_time_to_target_minutes(
                cabin_temp_c=cabin_temp_c,
                target_temp_c=float(s.target_temp_c),
                outside_temp_c=out,
                power_w=_finite(getattr(car_status, "instant_power_w", None)),
            )
            s.predicted_eta_minutes = float(eta_min) if eta_min is not None else None
            s.unreachable = eta_min is None

            if eta_min is None:
                planned_start = now
            else:
                planned_start = ready_by_dt - timedelta(minutes=float(eta_min))
                if planned_start < now:
                    planned_start = now

            s.planned_start_ts = planned_start.replace(microsecond=0).isoformat(sep=" ")

            # Outcome: first time we reach target (can happen before ready_by).
            if s.reached_ts is None and cabin_temp_c >= float(s.target_temp_c):
                offset_min = (now - ready_by_dt).total_seconds() / 60.0
                tol = float(self._cfg.reach_tolerance_minutes)
                s.reached_ts = now.replace(microsecond=0).isoformat(sep=" ")
                s.reached_offset_min = float(offset_min)
                s.reached_cabin_temp_c = float(cabin_temp_c)
                s.reached_outside_temp_c = float(out)
                s.reached_too_early = offset_min < -tol
                s.reached_too_late = offset_min > tol
                logger.info(
                    "ready_by: target reached (offset=%.1f min, early=%s late=%s)",
                    offset_min,
                    s.reached_too_early,
                    s.reached_too_late,
                )

            heater_on = bool(getattr(car_status, "is_heater_on", False))
            if now >= planned_start and not heater_on:
                
                s.status = "running"
                self._queue_command("turn_on")
            elif heater_on and now >= ready_by_dt and cabin_temp_c >= float(s.target_temp_c):
                # Mark completed once we reach target at/after ready_by and the heater was on.
                s.status = "completed"
            self._persist_if_changed(is_test=is_test)

    def _queue_command(self, action: str) -> None:
        now = datetime.now(self._tz).replace(microsecond=0)
        if self._schedule is None:
            return
        if self._schedule.last_command_ts is not None:
            last_dt = _dt_local(self._schedule.last_command_ts, self._tz)
            if last_dt is not None:
                if (now - last_dt).total_seconds() < float(self._cfg.command_cooldown_s):
                    return
        self._car_heater_service.queue_command({"action": action})
        self._schedule.last_command = action
        self._schedule.last_command_ts = now.isoformat(sep=" ")

    def _load_config_from_env(self) -> ReadyByConfig:
        def _get_bool(name: str, default: bool) -> bool:
            raw = (os.getenv(name) or "").strip().lower()
            if raw == "":
                return default
            return raw in ("1", "true", "yes", "y", "on")

        def _get_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _get_float(name: str, default: float) -> float:
            raw = os.getenv(name)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        return ReadyByConfig(
            enabled=_get_bool("CAR_HEATER_READY_BY_ENABLED", True),
            command_cooldown_s=_get_int("CAR_HEATER_READY_BY_COMMAND_COOLDOWN_S", 30),
            reach_tolerance_minutes=_get_float("CAR_HEATER_READY_BY_REACH_TOLERANCE_MIN", 2.0),
        )

    def _restore_from_db(self) -> None:
        if self._ctrl is None:
            return
        try:
            row = self._ctrl.get_ready_by_state()
        except Exception:
            logger.debug("ready_by: failed to read persisted state", exc_info=True)
            return
        if row is None or not row.state_json:
            return
        try:
            data = json.loads(row.state_json)
            sched = data.get("schedule")
            if isinstance(sched, dict):
                self._schedule = self._schedule_from_dict(sched)
                logger.info("ready_by: restored schedule from DB (status=%s)", self._schedule.status)
            self._last_persisted_json = row.state_json
        except Exception:
            logger.debug("ready_by: failed to parse persisted state_json", exc_info=True)

    @staticmethod
    def _schedule_from_dict(data: dict[str, Any]) -> ReadyBySchedule:
        allowed = {f.name for f in fields(ReadyBySchedule)}
        clean = {k: v for (k, v) in data.items() if k in allowed}
        return ReadyBySchedule(**clean)  # type: ignore[arg-type]

    def _persist_if_changed(self, *, is_test: bool) -> None:
        if is_test:
            return
        if self._ctrl is None:
            return
        state = {
            "schema_version": 1,
            "schedule": asdict(self._schedule) if self._schedule else None,
        }
        state_json = json.dumps(state, separators=(",", ":"), sort_keys=True)
        if self._last_persisted_json == state_json:
            return
        self._last_persisted_json = state_json
        try:
            self._ctrl.save_ready_by_state(
                CarHeaterReadyByState(
                    id=1,
                    state_json=state_json,
                    updated_ts=datetime.now(self._tz).replace(microsecond=0).isoformat(sep=" "),
                )
            )
        except Exception:
            logger.exception("ready_by: failed to persist state")
