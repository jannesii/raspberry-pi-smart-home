from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
import math
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from ...core import CarHeaterStatus
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
        weather_service: WeatherService | None = None,
        tz_name: str = "Europe/Helsinki",
        command_cooldown_s: int = 30,
    ) -> None:
        self._lock = RLock()
        self._tz = ZoneInfo(tz_name)
        self._car_heater_service = car_heater_service
        self._kfactor = kfactor_calibrator
        self._weather = weather_service
        self._command_cooldown_s = int(command_cooldown_s)

        self._schedule: ReadyBySchedule | None = None

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
            return ReadyBySchedule(**asdict(self._schedule))

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {"schedule": asdict(self._schedule) if self._schedule else None}

    def tick(
        self,
        car_status: CarHeaterStatus,
        *,
        outside_temp_c: float | None = None,
    ) -> None:
        """Evaluate schedule and queue commands as needed."""
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

            heater_on = bool(getattr(car_status, "is_heater_on", False))
            if now >= planned_start and not heater_on:
                s.status = "running"
                self._queue_command("turn_on")
            elif heater_on and now >= ready_by_dt and cabin_temp_c >= float(s.target_temp_c):
                # Mark completed once we reach target at/after ready_by.
                s.status = "completed"

    def _queue_command(self, action: str) -> None:
        now = datetime.now(self._tz).replace(microsecond=0)
        if self._schedule is None:
            return
        if self._schedule.last_command_ts is not None:
            last_dt = _dt_local(self._schedule.last_command_ts, self._tz)
            if last_dt is not None:
                if (now - last_dt).total_seconds() < float(self._command_cooldown_s):
                    return
        self._car_heater_service.queue_command({"action": action})
        self._schedule.last_command = action
        self._schedule.last_command_ts = now.isoformat(sep=" ")

