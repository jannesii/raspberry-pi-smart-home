"""
KFactor session management.

Handles session lifecycle: start, append samples, detect disturbances, finalize.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from statistics import mean
from typing import TYPE_CHECKING, Any

from .constants import (
    bucket_key,
    clamp,
    iso_no_micros,
)
from .models import KFactorConfig, KFactorFit, KFactorLastSession, KFactorSample

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from app.core import Controller

    from .physics import ThermalPhysics

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages calibration session lifecycle."""

    def __init__(
        self,
        cfg: KFactorConfig,
        ctrl: Controller | None,
        physics: ThermalPhysics,
        tz: ZoneInfo,
        get_active_params_fn,
    ) -> None:
        logger.debug(
            "SessionManager.__init__ called cfg=%s ctrl=%s physics=%s tz=%s",
            cfg,
            ctrl,
            physics,
            tz,
        )
        self._cfg = cfg
        self._ctrl = ctrl
        self._physics = physics
        self._tz = tz
        self._get_active_params = get_active_params_fn

        # Session state
        self._session_started_at: datetime | None = None
        self._session_window_date: str | None = None
        self._session_window_start: str | None = None
        self._session_window_stop: str | None = None
        self._session_flags: dict[str, Any] = {}
        self._session_samples: list[KFactorSample] = []
        self._is_autonomous_session: bool = False

        # Last session info
        self._last_session: KFactorLastSession | None = None

        # Active params override (for test mode)
        self._active_params_override: tuple[float, float] | None = None
        self._bucket_params_override: dict[tuple[int, int | None], tuple[float, float]] = {}

        self._load_last_session_from_db()

    def _load_last_session_from_db(self) -> None:
        """Load last known session summary from DB for snapshots."""
        logger.debug("kfactor: _load_last_session_from_db called")
        if self._ctrl is None:
            logger.debug("kfactor: _load_last_session_from_db skipped (no ctrl)")
            return
        try:
            sessions = self._ctrl.get_recent_kfactor_sessions(limit=1)
            if not sessions:
                logger.debug("kfactor: no kfactor sessions found in DB")
                return
            session = sessions[0]
            flags: dict[str, Any] = {}
            if session.flags_json:
                try:
                    flags = json.loads(session.flags_json)
                except Exception:
                    logger.debug(
                        "kfactor: failed to parse flags_json for session_id=%s",
                        session.id,
                        exc_info=True,
                    )
                    flags = {}
            reason = flags.get("stop_reason", "unknown")

            fit = None
            if session.id is not None:
                result = self._ctrl.get_kfactor_result_for_session(session_id=int(session.id))
            else:
                result = None
            if result is not None:
                rmse = float(result.rmse_C) if result.rmse_C is not None else float("nan")
                fit = KFactorFit(
                    k_loss_W_per_K=float(result.k_loss_W_per_K),
                    eta=float(result.eta),
                    rmse_C=rmse,
                    r2=result.r2,
                )

            self._last_session = KFactorLastSession(
                started_ts=session.start_ts,
                ended_ts=session.end_ts,
                sample_count=int(session.sample_count or 0),
                accepted=bool(session.accepted),
                quality_score=float(session.quality_score or 0.0),
                reason=reason,
                flags=flags,
                fit=fit,
            )
            logger.debug(
                "kfactor: loaded last_session from DB session_id=%s accepted=%s",
                session.id,
                session.accepted,
            )
        except Exception:
            logger.debug("kfactor: failed to load last session from DB", exc_info=True)

    @property
    def samples(self) -> list[KFactorSample]:
        return self._session_samples

    @property
    def started_at(self) -> datetime | None:
        return self._session_started_at

    @property
    def last_session(self) -> KFactorLastSession | None:
        return self._last_session

    @property
    def is_autonomous(self) -> bool:
        return self._is_autonomous_session

    @is_autonomous.setter
    def is_autonomous(self, value: bool) -> None:
        self._is_autonomous_session = value

    @property
    def active_params_override(self) -> tuple[float, float] | None:
        return self._active_params_override

    @property
    def bucket_params_override(self) -> dict[tuple[int, int | None], tuple[float, float]]:
        return self._bucket_params_override

    def start_session(
        self,
        *,
        now: datetime,
        window_start: datetime | None,
        window_stop: datetime | None,
        cabin_temp_c: float,
        heater_on: bool,
        power_w: float,
        outside_temp_c: float,
        wind_m_s: float | None,
        is_autonomous: bool,
    ) -> None:
        """Start a new calibration session."""
        self._is_autonomous_session = is_autonomous
        self._session_started_at = now
        self._session_window_date = window_start.date().isoformat() if window_start else None
        self._session_window_start = self._cfg.auto_calib_start_hhmm if window_start else None
        self._session_window_stop = self._cfg.auto_calib_stop_hhmm if window_stop else None
        self._session_flags = {"autonomous": is_autonomous}
        self._session_samples = []

        self.append_sample(
            ts=now,
            cabin_temp_c=cabin_temp_c,
            heater_on=heater_on,
            power_w=power_w,
            outside_temp_c=outside_temp_c,
            wind_m_s=wind_m_s,
        )

        mode = "AUTONOMOUS" if is_autonomous else "PASSIVE"
        logger.info(
            "kfactor: %s session started (Tout=%.1f°C, cabin=%.1f°C)",
            mode,
            outside_temp_c,
            cabin_temp_c,
        )

    def append_sample(
        self,
        *,
        ts: datetime,
        cabin_temp_c: float,
        heater_on: bool,
        power_w: float,
        outside_temp_c: float,
        wind_m_s: float | None,
    ) -> None:
        """Append a sample to the current session."""
        logger.debug(
            "kfactor: sample appended (ts=%s, T_cabin=%.2fC, heater_on=%s, power=%.1fW, T_out=%.2fC, wind=%.2f m/s)",
            ts.isoformat(),
            cabin_temp_c,
            heater_on,
            power_w,
            outside_temp_c,
            wind_m_s if wind_m_s is not None else float("nan"),
        )
        self._session_samples.append(
            KFactorSample(
                ts=ts,
                cabin_temp_c=float(cabin_temp_c),
                heater_on=bool(heater_on),
                power_w=float(power_w),
                outside_temp_c=float(outside_temp_c),
                wind_m_s=float(wind_m_s) if wind_m_s is not None else None,
            )
        )

    def session_duration_s(self, now: datetime) -> int | None:
        """Return current session duration in seconds."""
        if self._session_started_at is None:
            return None
        return int((now - self._session_started_at).total_seconds())

    def detect_disturbance(self) -> str | None:
        """Detect session disturbances (spikes, non-monotonic rise, etc.)."""
        samples = self._session_samples
        if len(samples) < 3:
            return None

        # Spike outliers (sensor glitches)
        last = samples[-1]
        prev = samples[-2]
        dT = last.cabin_temp_c - prev.cabin_temp_c
        if abs(dT) >= float(self._cfg.spike_threshold_C):
            self._session_flags["spike_outlier"] = {
                "dT": dT,
                "threshold": self._cfg.spike_threshold_C,
            }
            return "disturbance_spike"

        # Early non-monotonic rise check
        started = self._session_started_at
        if started is None:
            return None

        early_s = int(self._cfg.early_window_minutes) * 60

        # If we don't see a meaningful rise early on, abort
        if (samples[-1].ts - started).total_seconds() >= early_s:
            temp_rise = max(s.cabin_temp_c for s in samples) - samples[0].cabin_temp_c
            min_rise = max(0.3, float(self._cfg.min_temp_rise_C) * 0.25)
            if temp_rise < min_rise:
                self._session_flags["no_heating_detected"] = {
                    "temp_rise": temp_rise,
                    "min_rise": min_rise,
                    "window_s": early_s,
                }
                return "disturbance_no_heating"

        if (samples[-1].ts - started).total_seconds() <= early_s:
            drops = 0
            thr = float(self._cfg.drop_threshold_C)
            for i in range(1, len(samples)):
                if samples[i].cabin_temp_c < (samples[i - 1].cabin_temp_c - thr):
                    drops += 1
            if drops >= 2:
                self._session_flags["non_monotonic_early"] = {"drops": drops, "threshold": thr}
                return "disturbance_non_monotonic"

        return None

    def reset(self, reason: str) -> None:
        """Reset session state."""
        self._is_autonomous_session = False
        self._session_started_at = None
        self._session_window_date = None
        self._session_window_start = None
        self._session_window_stop = None
        self._session_flags = {"reason": reason}
        self._session_samples = []

    def finalize_session(
        self,
        *,
        end_ts: datetime,
        reason: str,
        is_test: bool,
        set_cooldown_fn,
    ) -> None:
        """Finalize and process a calibration session."""
        from app.services.alert_webhook import record_alert

        started = self._session_started_at
        samples = list(self._session_samples)
        is_autonomous = self._is_autonomous_session

        # Set cooldown
        set_cooldown_fn(end_ts, is_autonomous, reason)

        # Reset live session state
        self._is_autonomous_session = False
        self._session_started_at = None
        self._session_samples = []

        if started is None or len(samples) < 2:
            self._last_session = KFactorLastSession(
                started_ts=iso_no_micros(end_ts),
                ended_ts=iso_no_micros(end_ts),
                sample_count=len(samples),
                accepted=False,
                quality_score=0.0,
                reason=reason,
                flags={"error": "empty_session", "autonomous": is_autonomous},
                fit=None,
            )
            return

        duration_s = int((end_ts - started).total_seconds())
        cabin_t_start = samples[0].cabin_temp_c
        cabin_t_end = samples[-1].cabin_temp_c
        cabin_t_max = max(s.cabin_temp_c for s in samples)
        delta_t = cabin_t_end - cabin_t_start

        outside_vals = [s.outside_temp_c for s in samples if math.isfinite(s.outside_temp_c)]
        wind_vals = [
            s.wind_m_s for s in samples if s.wind_m_s is not None and math.isfinite(s.wind_m_s)
        ]
        outside_mean = mean(outside_vals) if outside_vals else None
        outside_min = min(outside_vals) if outside_vals else None
        outside_max = max(outside_vals) if outside_vals else None
        wind_mean = mean(wind_vals) if wind_vals else None

        flags: dict[str, Any] = dict(self._session_flags)
        flags["stop_reason"] = reason
        flags["autonomous"] = is_autonomous

        # Alert for no heating detected
        if reason == "disturbance_no_heating" and not is_test:
            details = flags.get("no_heating_detected", {})
            record_alert(
                key="kfactor_no_heating",
                title="KFactor calibration aborted (no heating detected)",
                message=(
                    f"duration_s={duration_s} "
                    f"temp_rise={details.get('temp_rise')} "
                    f"min_rise={details.get('min_rise')} "
                    f"window_s={details.get('window_s')} "
                    f"cabin_start={cabin_t_start:.2f}C "
                    f"cabin_end={cabin_t_end:.2f}C "
                    f"outside_mean={outside_mean}"
                ),
            )

        # Check informativeness
        informative, info_details = self._is_informative_session(
            samples=samples,
            started=started,
            ended=end_ts,
        )
        if not informative:
            flags["not_informative"] = info_details

        # Fit parameters
        active_before_k, active_before_eta = self._get_active_params()
        fit = (
            self._physics.fit_params(samples, active_before_k, active_before_eta)
            if informative
            else None
        )
        rmse = fit.rmse_C if fit is not None else float("inf")

        # Score and accept
        quality = self._quality_score(
            duration_s=duration_s, delta_t=delta_t, flags=flags, rmse_C=rmse
        )
        accepted = self._accept_session(
            duration_s=duration_s, delta_t=delta_t, flags=flags, quality_score=quality
        )

        flags_json = json.dumps(flags, separators=(",", ":"), sort_keys=True)

        promoted = False
        active_after: dict[str, Any] | None = None

        if fit is not None and accepted:
            new_k, new_eta, alpha = self._compute_weighted_update(
                old_k=active_before_k,
                old_eta=active_before_eta,
                fit=fit,
                quality_score=quality,
            )
            active_after = {"k_loss_W_per_K": new_k, "eta": new_eta, "alpha": alpha}
            promoted = True

            # In test mode, keep params in memory
            if is_test or self._ctrl is None:
                self._active_params_override = (new_k, new_eta)

            self._update_bucket_params(
                fit=fit,
                outside_mean=outside_mean,
                wind_mean=wind_mean,
                updated_at=end_ts,
                is_test=is_test,
            )

        self._last_session = KFactorLastSession(
            started_ts=iso_no_micros(started),
            ended_ts=iso_no_micros(end_ts),
            sample_count=len(samples),
            accepted=accepted,
            quality_score=quality,
            reason=reason,
            flags=flags,
            fit=fit,
        )

        if fit is not None and accepted:
            logger.info(
                "kfactor: session accepted (quality=%.2f rmse=%.3f k_loss=%.1f eta=%.3f)",
                quality,
                fit.rmse_C,
                fit.k_loss_W_per_K,
                fit.eta,
            )
        else:
            logger.info(
                "kfactor: session rejected (quality=%.2f duration=%ss deltaT=%.2f reason=%s)",
                quality,
                duration_s,
                delta_t,
                reason,
            )

        if is_test:
            self._append_test_output(
                {
                    "type": "kfactor_session",
                    "created_ts": iso_no_micros(end_ts),
                    "session": {
                        "start_ts": iso_no_micros(started),
                        "end_ts": iso_no_micros(end_ts),
                        "duration_s": duration_s,
                        "sample_count": len(samples),
                        "heater_mode": "ON_continuous",
                        "outside_t_mean": outside_mean,
                        "outside_t_min": outside_min,
                        "outside_t_max": outside_max,
                        "wind_mean": wind_mean,
                        "cabin_t_start": cabin_t_start,
                        "cabin_t_end": cabin_t_end,
                        "cabin_t_max": cabin_t_max,
                        "delta_t": delta_t,
                        "accepted": accepted,
                        "quality_score": quality,
                        "flags": flags,
                        "flags_json": flags_json,
                    },
                    "fit": asdict(fit) if fit is not None else None,
                    "active_params_before": {
                        "k_loss_W_per_K": active_before_k,
                        "eta": active_before_eta,
                    },
                    "active_params_after": active_after,
                    "promoted": promoted,
                }
            )
            return

        # Persist to DB
        self._persist_session(
            started=started,
            end_ts=end_ts,
            duration_s=duration_s,
            samples=samples,
            outside_mean=outside_mean,
            outside_min=outside_min,
            outside_max=outside_max,
            wind_mean=wind_mean,
            cabin_t_start=cabin_t_start,
            cabin_t_end=cabin_t_end,
            cabin_t_max=cabin_t_max,
            flags_json=flags_json,
            quality=quality,
            accepted=accepted,
            fit=fit,
        )

    def _persist_session(
        self,
        *,
        started: datetime,
        end_ts: datetime,
        duration_s: int,
        samples: list[KFactorSample],
        outside_mean: float | None,
        outside_min: float | None,
        outside_max: float | None,
        wind_mean: float | None,
        cabin_t_start: float,
        cabin_t_end: float,
        cabin_t_max: float,
        flags_json: str,
        quality: float,
        accepted: bool,
        fit: KFactorFit | None,
    ) -> None:
        """Persist session results to database."""
        from app.core.models import (
            CarHeaterKFactorResult,
            CarHeaterKFactorSession,
        )

        try:
            if self._ctrl is None:
                return

            session_row = CarHeaterKFactorSession(
                id=None,
                start_ts=iso_no_micros(started),
                end_ts=iso_no_micros(end_ts),
                auto_window_date=self._session_window_date,
                auto_window_start=self._session_window_start,
                auto_window_stop=self._session_window_stop,
                heater_mode="ON_continuous",
                outside_t_mean=outside_mean,
                outside_t_min=outside_min,
                outside_t_max=outside_max,
                wind_mean=wind_mean,
                cabin_t_start=cabin_t_start,
                cabin_t_end=cabin_t_end,
                cabin_t_max=cabin_t_max,
                duration_s=duration_s,
                sample_count=len(samples),
                flags_json=flags_json,
                quality_score=quality,
                accepted=accepted,
            )
            persisted_session = self._ctrl.record_kfactor_session(session_row)

            if fit is not None:
                if accepted:
                    promoted = self._update_active_params(
                        fit=fit, quality_score=quality, updated_at=end_ts
                    )
                else:
                    promoted = False

                result_row = CarHeaterKFactorResult(
                    id=None,
                    session_id=int(persisted_session.id or 0),
                    model_version=self._cfg.model_version,
                    k_loss_W_per_K=fit.k_loss_W_per_K,
                    eta=fit.eta,
                    rmse_C=fit.rmse_C,
                    r2=fit.r2,
                    confidence=None,
                    promoted=promoted,
                    created_ts=iso_no_micros(end_ts),
                )
                self._ctrl.record_kfactor_result(result_row)
        except Exception:
            logger.exception("kfactor: failed to persist calibration results")

    def _is_informative_session(
        self,
        *,
        samples: list[KFactorSample],
        started: datetime,
        ended: datetime,
    ) -> tuple[bool, dict[str, Any]]:
        """Check if session has enough curvature to be informative."""
        details: dict[str, Any] = {}

        duration_s = float((ended - started).total_seconds())
        details["duration_s"] = duration_s

        min_inf_s = float(int(self._cfg.informative_min_minutes) * 60)
        if duration_s < min_inf_s:
            details["reason"] = "duration_too_short_for_informativeness"
            details["min_informative_s"] = min_inf_s
            return False, details

        on = [s for s in samples if s.heater_on]
        if len(on) < 5:
            details["reason"] = "too_few_heater_on_samples"
            details["heater_on_samples"] = len(on)
            return False, details

        t1 = started + timedelta(minutes=1)
        t5 = started + timedelta(minutes=5)
        tend = on[-1].ts
        t_end_minus_5 = tend - timedelta(minutes=5)

        max_dist_s = float(self._cfg.curvature_max_sample_distance_s)
        T1 = self._temp_near_time(on, t1, max_distance_s=max_dist_s)
        T5 = self._temp_near_time(on, t5, max_distance_s=max_dist_s)
        Tend = self._temp_near_time(on, tend, max_distance_s=max_dist_s)
        T_end_minus_5 = self._temp_near_time(on, t_end_minus_5, max_distance_s=max_dist_s)

        if None in (T1, T5, Tend, T_end_minus_5):
            details["reason"] = "missing_samples_for_curvature"
            return False, details

        early_dt = (t5 - t1).total_seconds()
        late_dt = (tend - t_end_minus_5).total_seconds()
        if early_dt <= 0 or late_dt <= 0:
            details["reason"] = "invalid_time_deltas"
            return False, details

        early_slope = (float(T5) - float(T1)) / early_dt
        late_slope = (float(Tend) - float(T_end_minus_5)) / late_dt
        details["early_slope_C_per_s"] = early_slope
        details["late_slope_C_per_s"] = late_slope

        if not (math.isfinite(early_slope) and math.isfinite(late_slope)):
            details["reason"] = "non_finite_slopes"
            return False, details

        if early_slope <= 0:
            details["reason"] = "no_positive_early_slope"
            return False, details

        ratio = late_slope / early_slope
        details["late_over_early_ratio"] = ratio
        ratio_max = float(self._cfg.curvature_ratio_max)
        details["ratio_max"] = ratio_max

        if ratio >= ratio_max:
            details["reason"] = "insufficient_curvature"
            return False, details

        return True, details

    @staticmethod
    def _temp_near_time(
        samples: list[KFactorSample],
        ts: datetime,
        *,
        max_distance_s: float,
    ) -> float | None:
        """Return cabin temp for sample closest to ts."""
        if not samples:
            return None
        best = None
        best_dt = None
        for s in samples:
            dt = abs((s.ts - ts).total_seconds())
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best = s
        if best is None or best_dt is None:
            return None
        if best_dt > float(max_distance_s):
            return None
        return float(best.cabin_temp_c)

    def _quality_score(
        self,
        *,
        duration_s: int,
        delta_t: float,
        flags: dict[str, Any],
        rmse_C: float,
    ) -> float:
        """Compute session quality score."""
        min_s = int(self._cfg.min_session_minutes) * 60
        ideal_s = max(min_s + 10 * 60, min_s * 2)
        if duration_s < min_s:
            duration_score = 0.0
        else:
            duration_score = clamp((duration_s - min_s) / float(ideal_s - min_s), 0.0, 1.0)

        if delta_t <= 0:
            delta_score = 0.0
        else:
            delta_score = clamp(delta_t / float(self._cfg.min_temp_rise_C * 2.0), 0.0, 1.0)

        disturbance = any(k.startswith("non_monotonic") or k.startswith("spike") for k in flags)
        smoothness_score = 0.0 if disturbance else 1.0

        informative_score = 0.0 if "not_informative" in flags else 1.0
        continuity_score = 1.0  # Only record continuous heater-on sessions

        if not math.isfinite(rmse_C):
            fit_score = 0.0
        else:
            fit_score = clamp(1.0 - (rmse_C / 1.5), 0.0, 1.0)

        return float(
            mean(
                [
                    duration_score,
                    delta_score,
                    smoothness_score,
                    informative_score,
                    continuity_score,
                    fit_score,
                ]
            )
        )

    def _accept_session(
        self,
        *,
        duration_s: int,
        delta_t: float,
        flags: dict[str, Any],
        quality_score: float,
    ) -> bool:
        """Determine if session should be accepted."""
        min_s = int(self._cfg.min_session_minutes) * 60
        if duration_s < min_s:
            return False
        if delta_t < float(self._cfg.min_temp_rise_C):
            return False
        if "non_monotonic_early" in flags or "spike_outlier" in flags:
            return False
        if "no_heating_detected" in flags:
            return False
        if "not_informative" in flags:
            return False
        return quality_score >= float(self._cfg.quality_threshold)

    def _compute_weighted_update(
        self,
        *,
        old_k: float,
        old_eta: float,
        fit: KFactorFit,
        quality_score: float,
    ) -> tuple[float, float, float]:
        """Compute weighted parameter update."""
        alpha = clamp(
            float(quality_score) * float(self._cfg.base_alpha),
            float(self._cfg.alpha_min),
            float(self._cfg.alpha_max),
        )
        new_k = (1.0 - alpha) * float(old_k) + alpha * float(fit.k_loss_W_per_K)
        new_eta = (1.0 - alpha) * float(old_eta) + alpha * float(fit.eta)
        return new_k, new_eta, alpha

    def _update_bucket_params(
        self,
        *,
        fit: KFactorFit,
        outside_mean: float | None,
        wind_mean: float | None,
        updated_at: datetime,
        is_test: bool,
    ) -> None:
        """Update bucket-specific parameters."""
        from app.core.models import CarHeaterKFactorBucketParams

        if outside_mean is None:
            return
        try:
            t_bucket, wind_bucket = bucket_key(
                float(outside_mean),
                float(wind_mean) if wind_mean is not None else None,
            )
        except Exception:
            return

        self._bucket_params_override[(t_bucket, wind_bucket)] = (
            float(fit.k_loss_W_per_K),
            float(fit.eta),
        )

        if is_test or self._ctrl is None:
            return

        try:
            self._ctrl.save_kfactor_bucket_params(
                CarHeaterKFactorBucketParams(
                    id=None,
                    t_bucket=int(t_bucket),
                    wind_bucket=int(wind_bucket) if wind_bucket is not None else None,
                    k_loss_W_per_K=float(fit.k_loss_W_per_K),
                    eta=float(fit.eta),
                    updated_ts=iso_no_micros(updated_at),
                    source="bucket_update",
                )
            )
        except Exception:
            logger.debug("kfactor: failed to persist bucket params", exc_info=True)

    def _update_active_params(
        self,
        *,
        fit: KFactorFit,
        quality_score: float,
        updated_at: datetime,
    ) -> bool:
        """Update active (global) parameters."""
        from app.core.models import CarHeaterKFactorActiveParams
        from app.services.alert_webhook import record_alert

        if self._ctrl is None:
            return False

        old_k, old_eta = self._get_active_params()
        new_k, new_eta, _alpha = self._compute_weighted_update(
            old_k=old_k,
            old_eta=old_eta,
            fit=fit,
            quality_score=quality_score,
        )

        params = CarHeaterKFactorActiveParams(
            id=1,
            k_loss_W_per_K=new_k,
            eta=new_eta,
            updated_ts=iso_no_micros(updated_at),
            source="weighted_update",
        )
        self._ctrl.save_kfactor_active_params(params)
        self._active_params_override = (float(new_k), float(new_eta))

        if (
            fit.k_loss_W_per_K < float(self._cfg.k_loss_min)
            or fit.k_loss_W_per_K > float(self._cfg.k_loss_max)
            or fit.eta < float(self._cfg.eta_min)
            or fit.eta > float(self._cfg.eta_max)
        ):
            record_alert(
                key="kfactor_params_out_of_bounds",
                title="KFactor fit out of bounds",
                message=(
                    f"k_loss={fit.k_loss_W_per_K:.2f} "
                    f"eta={fit.eta:.3f} "
                    f"bounds_k=[{self._cfg.k_loss_min},{self._cfg.k_loss_max}] "
                    f"bounds_eta=[{self._cfg.eta_min},{self._cfg.eta_max}]"
                ),
            )
        return True

    def _append_test_output(self, record: dict[str, Any]) -> None:
        """Append test output to file."""
        path = (self._cfg.test_output_path or "").strip()
        if not path:
            return
        try:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("kfactor: failed to write test output to %r", path)
