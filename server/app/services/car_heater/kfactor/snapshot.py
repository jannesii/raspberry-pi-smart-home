"""
KFactor snapshot generation.

Generates debug and extended status snapshots for UI and diagnostics.
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict
from datetime import datetime
from typing import Any, TYPE_CHECKING

from .constants import (
    CABIN_VOLUME_M3,
    HEAT_CAPACITY_J_PER_K,
    HEATER_POWER_W,
    iso_no_micros,
)
from .models import KFactorConfig, KFactorLastSession, KFactorSample

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo
    from .physics import ThermalPhysics

logger = logging.getLogger(__name__)


class SnapshotGenerator:
    """Generates debug and extended status snapshots."""

    def __init__(
        self,
        cfg: KFactorConfig,
        tz: "ZoneInfo",
    ) -> None:
        self._cfg = cfg
        self._tz = tz

    def get_debug_snapshot(
        self,
        *,
        state: str,
        active_k: float,
        active_eta: float,
        session_started_at: datetime | None,
        session_samples: list[KFactorSample],
        is_autonomous_session: bool,
        cooldown_until: datetime | None,
        last_session: KFactorLastSession | None,
        slow_rise_checks: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a debug status snapshot."""
        if now is None:
            now = datetime.now(tz=self._tz)

        active_params = {
            "k_loss_W_per_K": active_k,
            "eta": active_eta,
        }

        session_info: dict[str, Any] | None = None
        if session_started_at is not None:
            elapsed_s = (now - session_started_at).total_seconds()
            session_info = {
                "started_at": iso_no_micros(session_started_at),
                "elapsed_s": int(elapsed_s),
                "sample_count": len(session_samples),
                "is_autonomous": is_autonomous_session,
            }

        return {
            "state": state,
            "enabled": bool(self._cfg.enabled),
            "active_params": active_params,
            "session": session_info,
            "cooldown_until": iso_no_micros(cooldown_until) if cooldown_until else None,
            "last_session": asdict(last_session) if last_session else None,
            "slow_rise_checks": slow_rise_checks,
            "now": iso_no_micros(now),
        }

    def get_extended_snapshot(
        self,
        *,
        state: str,
        active_k: float,
        active_eta: float,
        session_started_at: datetime | None,
        session_samples: list[KFactorSample],
        is_autonomous_session: bool,
        cooldown_until: datetime | None,
        last_session: KFactorLastSession | None,
        slow_rise_checks: int,
        physics: "ThermalPhysics",
        cabin_temp_c: float | None,
        outside_temp_c: float | None,
        is_heater_on: bool,
        target_temp_c: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an extended snapshot for UI (includes live predictions, physics info)."""
        if now is None:
            now = datetime.now(tz=self._tz)

        base = self.get_debug_snapshot(
            state=state,
            active_k=active_k,
            active_eta=active_eta,
            session_started_at=session_started_at,
            session_samples=session_samples,
            is_autonomous_session=is_autonomous_session,
            cooldown_until=cooldown_until,
            last_session=last_session,
            slow_rise_checks=slow_rise_checks,
            now=now,
        )

        # Physics constants
        base["physics"] = {
            "CABIN_VOLUME_M3": CABIN_VOLUME_M3,
            "HEAT_CAPACITY_J_PER_K": HEAT_CAPACITY_J_PER_K,
            "HEATER_POWER_W": HEATER_POWER_W,
            "mass_factor": self._cfg.mass_factor,
            "mass_factor_min": self._cfg.mass_factor_min,
            "mass_factor_max": self._cfg.mass_factor_max,
        }

        # Config summary
        base["config"] = {
            "enabled": self._cfg.enabled,
            "auto_calib_start_hhmm": self._cfg.auto_calib_start_hhmm,
            "auto_calib_stop_hhmm": self._cfg.auto_calib_stop_hhmm,
            "min_session_minutes": self._cfg.min_session_minutes,
            "max_session_minutes": self._cfg.max_session_minutes,
            "autonomous_max_session_minutes": self._cfg.autonomous_max_session_minutes,
            "autonomous_cooldown_minutes": self._cfg.autonomous_cooldown_minutes,
            "cooldown_minutes": self._cfg.cooldown_minutes,
            "min_temp_rise_C": self._cfg.min_temp_rise_C,
            "quality_threshold": self._cfg.quality_threshold,
        }

        # Live ETA prediction
        eta_minutes: float | None = None
        if (
            cabin_temp_c is not None
            and outside_temp_c is not None
            and target_temp_c is not None
            and math.isfinite(cabin_temp_c)
            and math.isfinite(outside_temp_c)
            and math.isfinite(target_temp_c)
        ):
            eta_minutes = physics.predict_time_to_target_minutes(
                cabin_temp_c=cabin_temp_c,
                target_temp_c=target_temp_c,
                outside_temp_c=outside_temp_c,
                k_loss=active_k,
                eta=active_eta,
            )

        base["live"] = {
            "cabin_temp_c": cabin_temp_c,
            "outside_temp_c": outside_temp_c,
            "is_heater_on": is_heater_on,
            "target_temp_c": target_temp_c,
            "eta_minutes": eta_minutes,
        }

        # Sample history (last N samples)
        if session_samples:
            last_samples = session_samples[-10:]
            base["recent_samples"] = [
                {
                    "ts": iso_no_micros(s.ts),
                    "cabin_temp_c": s.cabin_temp_c,
                    "heater_on": s.heater_on,
                    "power_w": s.power_w,
                    "outside_temp_c": s.outside_temp_c,
                    "wind_m_s": s.wind_m_s,
                }
                for s in last_samples
            ]
        else:
            base["recent_samples"] = []

        return base
