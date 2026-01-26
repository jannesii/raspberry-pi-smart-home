"""
KFactor snapshot generation.

Generates debug and extended status snapshots for UI and diagnostics.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .constants import (
    AIR_DENSITY_KG_M3,
    CABIN_VOLUME_M3,
    HEAT_CAPACITY_J_PER_K,
    HEATER_POWER_W,
    SPECIFIC_HEAT_J_KG_K,
    dt_from_any,
    iso_no_micros,
)

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from app.core import Controller

    from .models import KFactorConfig, KFactorLastSession, KFactorSample
    from .physics import ThermalPhysics

logger = logging.getLogger(__name__)


class SnapshotGenerator:
    """Generates debug and extended status snapshots."""

    def __init__(
        self,
        cfg: KFactorConfig,
        tz: ZoneInfo,
        ctrl: Controller | None = None,
    ) -> None:
        logger.debug("SnapshotGenerator.__init__ called cfg=%s tz=%s ctrl=%s", cfg, tz, ctrl)
        self._cfg = cfg
        self._tz = tz
        self._ctrl = ctrl

    def get_debug_snapshot(
        self,
        *,
        state: str,
        active_k: float,
        active_eta: float,
        session_started_at: datetime | None,
        session_samples: list[KFactorSample],
        is_autonomous_session: bool,
        autonomous_cooldown_until: datetime | None,
        passive_cooldown_until: datetime | None,
        last_session: KFactorLastSession | None,
        slow_rise_checks: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a debug status snapshot."""
        logger.debug(
            "get_debug_snapshot called state=%s active_k=%s active_eta=%s session_started_at=%s samples=%s "
            "is_autonomous_session=%s autonomous_cooldown_until=%s passive_cooldown_until=%s "
            "last_session=%s slow_rise_checks=%s now=%s",
            state,
            active_k,
            active_eta,
            session_started_at,
            len(session_samples),
            is_autonomous_session,
            autonomous_cooldown_until,
            passive_cooldown_until,
            last_session,
            slow_rise_checks,
            now,
        )
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

        snapshot = {
            "state": state,
            "autonomous_enabled": bool(self._cfg.enabled),
            "enabled": bool(self._cfg.enabled),
            "is_autonomous_session": is_autonomous_session,
            "active_params": active_params,
            "session_started_at": iso_no_micros(session_started_at)
            if session_started_at is not None
            else None,
            "session_sample_count": len(session_samples),
            "session": session_info,
            "autonomous_cooldown_until": iso_no_micros(autonomous_cooldown_until)
            if autonomous_cooldown_until
            else None,
            "passive_cooldown_until": iso_no_micros(passive_cooldown_until)
            if passive_cooldown_until
            else None,
            "cooldown_until": iso_no_micros(autonomous_cooldown_until or passive_cooldown_until)
            if (autonomous_cooldown_until or passive_cooldown_until)
            else None,
            "last_session": asdict(last_session) if last_session else None,
            "slow_rise_counter": slow_rise_checks,
            "slow_rise_checks": slow_rise_checks,
            "now": iso_no_micros(now),
            "constants": {
                "cabin_volume_m3": CABIN_VOLUME_M3,
                "heater_power_W": HEATER_POWER_W,
                "air_density_kg_m3": AIR_DENSITY_KG_M3,
                "specific_heat_J_kgK": SPECIFIC_HEAT_J_KG_K,
                "C_J_per_K": HEAT_CAPACITY_J_PER_K,
            },
            "config": asdict(self._cfg),
        }
        logger.debug("Generated debug snapshot: %s", snapshot)
        return snapshot

    def get_extended_snapshot(
        self,
        *,
        state: str,
        active_k: float,
        active_eta: float,
        session_started_at: datetime | None,
        session_samples: list[KFactorSample],
        is_autonomous_session: bool,
        autonomous_cooldown_until: datetime | None,
        passive_cooldown_until: datetime | None,
        last_session: KFactorLastSession | None,
        slow_rise_checks: int,
        physics: ThermalPhysics,
        cabin_temp_c: float | None,
        outside_temp_c: float | None,
        is_heater_on: bool,
        target_temp_c: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an extended snapshot for UI (includes live predictions, physics info)."""
        logger.debug(
            "get_extended_snapshot called state=%s active_k=%s active_eta=%s session_started_at=%s samples=%s "
            "is_autonomous_session=%s autonomous_cooldown_until=%s passive_cooldown_until=%s "
            "last_session=%s slow_rise_checks=%s cabin_temp_c=%s outside_temp_c=%s "
            "is_heater_on=%s target_temp_c=%s now=%s",
            state,
            active_k,
            active_eta,
            session_started_at,
            len(session_samples),
            is_autonomous_session,
            autonomous_cooldown_until,
            passive_cooldown_until,
            last_session,
            slow_rise_checks,
            cabin_temp_c,
            outside_temp_c,
            is_heater_on,
            target_temp_c,
            now,
        )
        if now is None:
            now = datetime.now(tz=self._tz)

        base = self.get_debug_snapshot(
            state=state,
            active_k=active_k,
            active_eta=active_eta,
            session_started_at=session_started_at,
            session_samples=session_samples,
            is_autonomous_session=is_autonomous_session,
            autonomous_cooldown_until=autonomous_cooldown_until,
            passive_cooldown_until=passive_cooldown_until,
            last_session=last_session,
            slow_rise_checks=slow_rise_checks,
            now=now,
        )

        # Physics constants
        base["physics"] = {
            "CABIN_VOLUME_M3": CABIN_VOLUME_M3,
            "HEAT_CAPACITY_J_PER_K": HEAT_CAPACITY_J_PER_K,
            "HEATER_POWER_W": HEATER_POWER_W,
            "AIR_DENSITY_KG_M3": AIR_DENSITY_KG_M3,
            "SPECIFIC_HEAT_J_KG_K": SPECIFIC_HEAT_J_KG_K,
            "mass_factor": self._cfg.mass_factor,
            "mass_factor_min": self._cfg.mass_factor_min,
            "mass_factor_max": self._cfg.mass_factor_max,
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

        # Live session metrics (legacy UI shape)
        live_session = None
        if state in ("PASSIVE_RECORDING", "AUTONOMOUS_RECORDING") and session_samples:
            samples = session_samples
            duration_s = (
                int((now - session_started_at).total_seconds()) if session_started_at else 0
            )
            first_sample = samples[0]
            last_sample = samples[-1]

            rise_rate = None
            if len(samples) >= 2:
                recent = samples[-min(5, len(samples)) :]
                time_span_s = (recent[-1].ts - recent[0].ts).total_seconds()
                if time_span_s > 0:
                    temp_change = recent[-1].cabin_temp_c - recent[0].cabin_temp_c
                    rise_rate = (temp_change / time_span_s) * 60.0

            live_session = {
                "active": True,
                "mode": "autonomous" if is_autonomous_session else "passive",
                "started_ts": iso_no_micros(session_started_at) if session_started_at else None,
                "duration_s": int(duration_s),
                "sample_count": len(samples),
                "cabin_temp_start": round(first_sample.cabin_temp_c, 1),
                "cabin_temp_current": round(last_sample.cabin_temp_c, 1),
                "cabin_temp_rise": round(last_sample.cabin_temp_c - first_sample.cabin_temp_c, 1),
                "outside_temp_current": round(last_sample.outside_temp_c, 1)
                if last_sample.outside_temp_c
                else None,
                "wind_current": round(last_sample.wind_m_s, 1) if last_sample.wind_m_s else None,
                "power_current": round(last_sample.power_w, 0) if last_sample.power_w else None,
                "rise_rate_c_per_min": round(rise_rate, 3) if rise_rate is not None else None,
                "slow_rise_counter": slow_rise_checks,
                "slow_rise_threshold": self._cfg.autonomous_slow_rise_samples,
            }

        base["live_session"] = live_session

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

        # Recent sessions with fit results
        recent_sessions = []
        if self._ctrl:
            try:
                sessions = self._ctrl.get_sessions_with_results(limit=10)
                logger.debug("get_extended_snapshot: got %d sessions", len(sessions))
                for s in sessions:
                    reason = None
                    if s.get("flags_json"):
                        try:
                            flags = json.loads(s["flags_json"])
                            reason = (
                                flags.get("rejection_reason")
                                or flags.get("end_reason")
                                or flags.get("stop_reason")
                            )
                        except Exception:
                            logger.debug(
                                "kfactor: failed to parse flags_json for session %s",
                                s.get("id"),
                                exc_info=True,
                            )

                    recent_sessions.append(
                        {
                            "id": s["id"],
                            "start_ts": s["start_ts"],
                            "end_ts": s["end_ts"],
                            "mode": s["mode"],
                            "duration_min": round(s["duration_s"] / 60, 1)
                            if s["duration_s"]
                            else None,
                            "cabin_t_start": s["cabin_t_start"],
                            "cabin_t_end": s["cabin_t_end"],
                            "outside_t_mean": s["outside_t_mean"],
                            "wind_mean": s["wind_mean"],
                            "quality_score": s["quality_score"],
                            "accepted": s["accepted"],
                            "reason": reason,
                            "fit": s["fit"],
                        }
                    )
            except Exception:
                logger.warning("kfactor: failed to get recent sessions", exc_info=True)

        logger.debug("get_extended_snapshot: returning %d recent_sessions", len(recent_sessions))
        base["recent_sessions"] = recent_sessions

        # Bucket coverage
        bucket_coverage = []
        if self._ctrl:
            try:
                buckets = self._ctrl.get_all_bucket_params()
                logger.debug("get_extended_snapshot: got %d buckets", len(buckets))
                now_local = datetime.now(self._tz)
                for b in buckets:
                    age_days = None
                    if b.updated_ts:
                        try:
                            updated = dt_from_any(b.updated_ts, self._tz)
                            if updated:
                                age_days = (now_local - updated).days
                        except Exception:
                            logger.debug(
                                "kfactor: failed to calculate age_days for bucket t=%s",
                                b.t_bucket,
                                exc_info=True,
                            )

                    bucket_coverage.append(
                        {
                            "t_bucket": b.t_bucket,
                            "wind_bucket": b.wind_bucket,
                            "k_loss": round(b.k_loss_W_per_K, 1) if b.k_loss_W_per_K else None,
                            "eta": round(b.eta, 3) if b.eta else None,
                            "updated_ts": b.updated_ts,
                            "age_days": age_days,
                        }
                    )
            except Exception:
                logger.warning("kfactor: failed to get bucket coverage", exc_info=True)

        logger.debug("get_extended_snapshot: returning %d bucket_coverage", len(bucket_coverage))
        base["bucket_coverage"] = bucket_coverage

        # Statistics
        statistics = {}
        if self._ctrl:
            try:
                statistics = self._ctrl.get_calibration_stats(lookback_days=7)
            except Exception:
                logger.debug("kfactor: failed to get statistics", exc_info=True)

        base["statistics"] = statistics

        logger.debug("Extended snapshot: %s", base)
        return base
