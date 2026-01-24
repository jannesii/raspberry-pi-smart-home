from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import logging
import math
import os
from statistics import mean
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from ...core import (
    CarHeaterKFactorActiveParams,
    CarHeaterKFactorResult,
    CarHeaterKFactorSession,
    CarHeaterStatus,
    Controller,
)
from ..weather.weather_service import WeatherService

logger = logging.getLogger(__name__)


# Fixed physical constants (defaults from TODO.md / plan)
CABIN_VOLUME_M3 = 2.8
HEATER_POWER_W = 1000.0
AIR_DENSITY_KG_M3 = 1.2
SPECIFIC_HEAT_J_KG_K = 1000.0
HEAT_CAPACITY_J_PER_K = AIR_DENSITY_KG_M3 * CABIN_VOLUME_M3 * SPECIFIC_HEAT_J_KG_K


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _finite(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def _dt_from_any(ts: Any, tz: ZoneInfo) -> datetime | None:
    if isinstance(ts, datetime):
        return ts.astimezone(tz) if ts.tzinfo else ts.replace(tzinfo=tz)
    if isinstance(ts, str) and ts.strip():
        # DB rows are stored like "YYYY-MM-DD HH:MM:SS+02:00"
        try:
            dt = datetime.fromisoformat(ts.strip())
            return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            return None
    return None


def _parse_hhmm(raw: str) -> tuple[int, int] | None:
    try:
        parts = raw.strip().split(":", 1)
        if len(parts) != 2:
            return None
        hh = int(parts[0])
        mm = int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh, mm
    except Exception:
        return None


def _iso_no_micros(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


@dataclass
class KFactorConfig:
    enabled: bool = True
    auto_calib_start_hhmm: str = "00:00"
    auto_calib_stop_hhmm: str = "23:59"
    grace_samples: int = 3
    min_session_minutes: int = 15
    max_session_minutes: int = 120
    min_temp_rise_C: float = 2.0
    early_window_minutes: int = 5
    drop_threshold_C: float = 0.5
    spike_threshold_C: float = 5.0
    quality_threshold: float = 0.6
    cooldown_minutes: int = 120
    bucket_lookback_days: int = 7
    # Effective thermal mass multiplier (air-only C is too small for real cars)
    mass_factor: float = 30.0
    mass_factor_min: float = 5.0
    mass_factor_max: float = 150.0
    eta_min: float = 0.05
    eta_max: float = 1.0
    k_loss_min: float = 10.0
    k_loss_max: float = 400.0
    default_eta: float = 0.6
    default_k_loss_W_per_K: float = 40.0
    # Outside temperature smoothing window (samples)
    tout_smooth_window: int = 3
    # Informativeness gating (curvature)
    informative_min_minutes: int = 20
    curvature_ratio_max: float = 0.6
    curvature_max_sample_distance_s: int = 120
    # Light priors to reduce parameter tradeoff on weak sessions
    prior_lambda_k: float = 0.1
    prior_lambda_eta: float = 0.1
    base_alpha: float = 0.3
    alpha_min: float = 0.05
    alpha_max: float = 0.5
    # Test-mode output (when /status/test is used, persist calibration artifacts to a file)
    test_output_path: str = "/home/jannesi/Code/server/app/services/car_heater/car_heater_kfactor_test_results.jsonl"
    model_version: str = "v1"


@dataclass(frozen=True)
class KFactorSample:
    ts: datetime
    cabin_temp_c: float
    heater_on: bool
    power_w: float
    outside_temp_c: float
    wind_m_s: float | None = None


@dataclass(frozen=True)
class KFactorFit:
    k_loss_W_per_K: float
    eta: float
    rmse_C: float
    r2: float | None


@dataclass(frozen=True)
class KFactorLastSession:
    started_ts: str
    ended_ts: str
    sample_count: int
    accepted: bool
    quality_score: float
    reason: str
    flags: dict[str, Any]
    fit: KFactorFit | None = None


class KFactorCalibrator:
    """Auto-calibrate (k_loss, eta) for cabin heating Ready-by prediction."""

    def __init__(
        self,
        *,
        ctrl: Controller,
        weather_service: WeatherService | None,
        config: KFactorConfig | None = None,
        tz_name: str = "Europe/Helsinki",
    ) -> None:
        self._tz = ZoneInfo(tz_name)
        self._ctrl: Controller = ctrl
        self._weather = weather_service
        self._cfg = config or self._load_config_from_env()

        self._lock = RLock()
        self._state: str = "IDLE"
        self._heater_on_streak: int = 0
        self._cooldown_until: datetime | None = None

        self._session_started_at: datetime | None = None
        self._session_window_date: str | None = None
        self._session_window_start: str | None = None
        self._session_window_stop: str | None = None
        self._session_flags: dict[str, Any] = {}
        self._session_samples: list[KFactorSample] = []
        self._last_session: KFactorLastSession | None = None
        self._active_params_override: tuple[float, float] | None = None

        logger.info(
            "KFactorCalibrator initialized (window=%s..%s min=%s max=%s)",
            self._cfg.auto_calib_start_hhmm,
            self._cfg.auto_calib_stop_hhmm,
            self._cfg.min_session_minutes,
            self._cfg.max_session_minutes,
        )

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def config(self) -> KFactorConfig:
        return self._cfg
    
    @property
    def is_enabled(self) -> bool:
        return self._cfg.enabled
    
    @is_enabled.setter
    def is_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._cfg.enabled = enabled
            logger.info("KFactorCalibrator enabled set to: %r", enabled)

    def tick(
        self,
        car_status: CarHeaterStatus,
        *,
        outside_temp_c: float | None = None,
        wind_m_s: float | None = None,
        is_test: bool = False,
    ) -> None:
        """Process a new car heater status sample (called on every status POST)."""
        if not self._cfg.enabled:
            return
        now = _dt_from_any(getattr(car_status, "timestamp", None), self._tz)
        if now is None:
            return

        cabin_temp_c = _finite(getattr(car_status, "ambient_temp", None))
        heater_on = bool(getattr(car_status, "is_heater_on", False))
        power_w_raw = _finite(getattr(car_status, "instant_power_w", None)) or 0.0

        # Determine outside temperature and wind (override wins).
        outside_temp_c = _finite(outside_temp_c)
        wind_m_s = _finite(wind_m_s)
        if outside_temp_c is None:
            try:
                if self._weather is not None:
                    w = self._weather.get_latest()
                    if w.t2m is not None:
                        outside_temp_c = _finite(w.t2m.value)
                    if wind_m_s is None and w.ws_10min is not None:
                        wind_m_s = _finite(w.ws_10min.value)
            except Exception:
                logger.debug("kfactor: weather lookup failed", exc_info=True)

        if outside_temp_c is None:
            return

        # Use Shelly power when it looks valid; otherwise fall back to the fixed constant.
        power_w = power_w_raw if power_w_raw > 1.0 else HEATER_POWER_W

        with self._lock:
            in_window, window_start, window_stop = self._is_in_window(now)
            if not in_window:
                if self._state != "IDLE":
                    self._reset("window ended")
                return

            if self._cooldown_until is not None and now < self._cooldown_until:
                self._state = "COOLDOWN"
                return
            if self._state == "COOLDOWN" and self._cooldown_until is not None and now >= self._cooldown_until:
                self._state = "ARMED"

            if self._state == "IDLE":
                self._state = "ARMED"

            if self._state == "ARMED":
                if not heater_on or cabin_temp_c is None:
                    self._heater_on_streak = 0
                    return

                self._heater_on_streak += 1
                if self._heater_on_streak < max(1, int(self._cfg.grace_samples)):
                    return

                if not is_test and not self.should_calibrate(now, outside_temp_c, wind_m_s):
                    return

                self._start_session(
                    now=now,
                    window_start=window_start,
                    window_stop=window_stop,
                    cabin_temp_c=cabin_temp_c,
                    heater_on=heater_on,
                    power_w=power_w,
                    outside_temp_c=outside_temp_c,
                    wind_m_s=wind_m_s,
                )
                return

            if self._state == "RECORDING":
                if cabin_temp_c is not None:
                    self._append_sample(
                        ts=now,
                        cabin_temp_c=cabin_temp_c,
                        heater_on=heater_on,
                        power_w=power_w,
                        outside_temp_c=outside_temp_c,
                        wind_m_s=wind_m_s,
                    )

                duration_s = self._session_duration_s(now)
                if not heater_on:
                    self._finalize_session(end_ts=now, reason="heater_off", is_test=is_test)
                    return
                if duration_s is not None and duration_s >= int(self._cfg.max_session_minutes) * 60:
                    self._finalize_session(end_ts=now, reason="max_duration", is_test=is_test)
                    return

                disturbance_reason = self._detect_disturbance()
                if disturbance_reason is not None:
                    self._finalize_session(end_ts=now, reason=disturbance_reason, is_test=is_test)
                    return

    def get_active_params(
        self,
        now_ts: datetime | None = None,
        outside_temp_c: float | None = None,
        wind_m_s: float | None = None,
    ) -> tuple[float, float]:
        """Return current best (k_loss, eta). Bucket logic can be added later."""
        with self._lock:
            if self._active_params_override is not None:
                return self._active_params_override
        params = None
        try:
            if self._ctrl is not None:
                params = self._ctrl.get_kfactor_active_params()
        except Exception:
            logger.debug("kfactor: get_active_params failed", exc_info=True)

        k_loss = _finite(getattr(params, "k_loss_W_per_K", None)) if params else None
        eta = _finite(getattr(params, "eta", None)) if params else None
        if k_loss is None:
            k_loss = float(self._cfg.default_k_loss_W_per_K)
        if eta is None:
            eta = float(self._cfg.default_eta)
        return k_loss, eta

    def should_calibrate(
        self,
        now_ts: datetime,
        outside_temp_c: float,
        wind_m_s: float | None = None,
    ) -> bool:
        """Return True if we should start a calibration session now."""
        t_bucket, wind_bucket = self._bucket_key(outside_temp_c, wind_m_s)
        lookback = timedelta(days=int(self._cfg.bucket_lookback_days))

        try:
            if self._ctrl is None:
                return True
            sessions = self._ctrl.get_recent_kfactor_sessions(
                limit=200, accepted_only=True)
        except Exception:
            logger.debug("kfactor: failed to query prior sessions", exc_info=True)
            return True

        for s in sessions:
            if not getattr(s, "accepted", False):
                continue
            if s.outside_t_mean is None:
                continue
            s_dt = _dt_from_any(s.start_ts, self._tz)
            if s_dt is None:
                continue
            if (now_ts - s_dt) > lookback:
                continue

            sb_t, sb_w = self._bucket_key(
                float(s.outside_t_mean),
                float(s.wind_mean) if s.wind_mean is not None else None,
            )
            # Wind handling:
            # - If current wind is missing, match any prior wind bucket for same temp bucket.
            # - If prior wind is missing, treat it as matching any current wind bucket.
            wind_matches = True
            if wind_bucket is None:
                wind_matches = True
            elif sb_w is None:
                wind_matches = True
            else:
                wind_matches = (sb_w == wind_bucket)

            if sb_t == t_bucket and wind_matches:
                logger.info(
                    "kfactor: skipped calibration (bucket covered: t=%s wind=%s)",
                    t_bucket,
                    wind_bucket,
                )
                return False

        return True

    def finalize_session(self, reason: str) -> None:
        """Force-end a recording session (best-effort)."""
        now = datetime.now(self._tz)
        with self._lock:
            if self._state != "RECORDING":
                return
            self._finalize_session(end_ts=now, reason=reason, is_test=False)

    def record_prediction_outcome(
        self,
        run_id: str,
        predicted_eta_minutes: float,
        actual_eta_minutes: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Placeholder for drift detection storage (future)."""
        _ = (run_id, predicted_eta_minutes, actual_eta_minutes, context)

    def predict_time_to_target_minutes(
        self,
        *,
        cabin_temp_c: float,
        target_temp_c: float,
        outside_temp_c: float,
        power_w: float | None = None,
        margin_c: float = 0.5,
    ) -> float | None:
        """Compute ETA (minutes) for reaching target temperature, or None if not reachable."""
        k_loss, eta = self.get_active_params()
        P = _finite(power_w) or HEATER_POWER_W
        C_eff = HEAT_CAPACITY_J_PER_K * _clamp(
            float(self._cfg.mass_factor),
            float(self._cfg.mass_factor_min),
            float(self._cfg.mass_factor_max),
        )

        if cabin_temp_c >= target_temp_c:
            return 0.0
        if k_loss <= 0.0:
            return None

        Tss = outside_temp_c + (eta * P) / k_loss
        if target_temp_c >= (Tss - margin_c):
            return None

        denom = (cabin_temp_c - Tss)
        if denom == 0:
            return None

        ratio = (target_temp_c - Tss) / denom
        if ratio <= 0.0 or ratio >= 1.0:
            return None

        try:
            t_s = -(C_eff / k_loss) * math.log(ratio)
        except Exception:
            return None
        if not math.isfinite(t_s) or t_s < 0:
            return None
        return t_s / 60.0

    def get_debug_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of internal state for debugging."""
        with self._lock:
            active_k_loss, active_eta = self.get_active_params()
            return {
                "state": self._state,
                "cooldown_until": _iso_no_micros(self._cooldown_until) if self._cooldown_until else None,
                "session_started_at": _iso_no_micros(self._session_started_at) if self._session_started_at else None,
                "session_sample_count": len(self._session_samples),
                "active_params": {
                    "k_loss_W_per_K": active_k_loss,
                    "eta": active_eta,
                },
                "last_session": asdict(self._last_session) if self._last_session else None,
                "constants": {
                    "cabin_volume_m3": CABIN_VOLUME_M3,
                    "heater_power_W": HEATER_POWER_W,
                    "air_density_kg_m3": AIR_DENSITY_KG_M3,
                    "specific_heat_J_kgK": SPECIFIC_HEAT_J_KG_K,
                    "C_J_per_K": HEAT_CAPACITY_J_PER_K,
                },
                "config": asdict(self._cfg),
            }

    # --------------------
    # Internals
    # --------------------
    def _load_config_from_env(self) -> KFactorConfig:
        def _get_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _get_float(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _get_str(name: str, default: str) -> str:
            raw = os.getenv(name)
            return raw.strip() if raw else default

        return KFactorConfig(
            enabled=_get_str("CAR_HEATER_KFACTOR_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y", "on"),
            auto_calib_start_hhmm=_get_str("CAR_HEATER_KFACTOR_AUTO_START_HHMM", "00:00"),
            auto_calib_stop_hhmm=_get_str("CAR_HEATER_KFACTOR_AUTO_STOP_HHMM", "23:59"),
            grace_samples=_get_int("CAR_HEATER_KFACTOR_GRACE_SAMPLES", 3),
            min_session_minutes=_get_int("CAR_HEATER_KFACTOR_MIN_SESSION_MINUTES", 15),
            max_session_minutes=_get_int("CAR_HEATER_KFACTOR_MAX_SESSION_MINUTES", 120),
            min_temp_rise_C=_get_float("CAR_HEATER_KFACTOR_MIN_TEMP_RISE_C", 2.0),
            early_window_minutes=_get_int("CAR_HEATER_KFACTOR_EARLY_WINDOW_MINUTES", 5),
            drop_threshold_C=_get_float("CAR_HEATER_KFACTOR_DROP_THRESHOLD_C", 0.5),
            spike_threshold_C=_get_float("CAR_HEATER_KFACTOR_SPIKE_THRESHOLD_C", 5.0),
            quality_threshold=_get_float("CAR_HEATER_KFACTOR_QUALITY_THRESHOLD", 0.6),
            cooldown_minutes=_get_int("CAR_HEATER_KFACTOR_COOLDOWN_MINUTES", 120),
            bucket_lookback_days=_get_int("CAR_HEATER_KFACTOR_BUCKET_LOOKBACK_DAYS", 7),
            mass_factor=_get_float("CAR_HEATER_KFACTOR_MASS_FACTOR", 30.0),
            mass_factor_min=_get_float("CAR_HEATER_KFACTOR_MASS_FACTOR_MIN", 5.0),
            mass_factor_max=_get_float("CAR_HEATER_KFACTOR_MASS_FACTOR_MAX", 150.0),
            default_eta=_get_float("CAR_HEATER_KFACTOR_DEFAULT_ETA", 0.6),
            default_k_loss_W_per_K=_get_float("CAR_HEATER_KFACTOR_DEFAULT_K_LOSS", 40.0),
            tout_smooth_window=_get_int("CAR_HEATER_KFACTOR_TOUT_SMOOTH_WINDOW", 3),
            informative_min_minutes=_get_int("CAR_HEATER_KFACTOR_INFORMATIVE_MIN_MINUTES", 20),
            curvature_ratio_max=_get_float("CAR_HEATER_KFACTOR_CURVATURE_RATIO_MAX", 0.6),
            curvature_max_sample_distance_s=_get_int("CAR_HEATER_KFACTOR_CURVATURE_MAX_SAMPLE_DISTANCE_S", 120),
            prior_lambda_k=_get_float("CAR_HEATER_KFACTOR_PRIOR_LAMBDA_K", 0.1),
            prior_lambda_eta=_get_float("CAR_HEATER_KFACTOR_PRIOR_LAMBDA_ETA", 0.1),
            base_alpha=_get_float("CAR_HEATER_KFACTOR_BASE_ALPHA", 0.3),
            alpha_min=_get_float("CAR_HEATER_KFACTOR_ALPHA_MIN", 0.05),
            alpha_max=_get_float("CAR_HEATER_KFACTOR_ALPHA_MAX", 0.5),
            test_output_path=_get_str(
                "CAR_HEATER_KFACTOR_TEST_OUTPUT_PATH",
                "/tmp/car_heater_kfactor_test_results.jsonl",
            ),
        )

    def _is_in_window(self, now: datetime) -> tuple[bool, datetime, datetime]:
        start_hhmm = _parse_hhmm(self._cfg.auto_calib_start_hhmm) or (0, 0)
        stop_hhmm = _parse_hhmm(self._cfg.auto_calib_stop_hhmm) or (23, 59)

        start = datetime(
            now.year, now.month, now.day, start_hhmm[0], start_hhmm[1], tzinfo=self._tz
        )
        stop = datetime(
            now.year, now.month, now.day, stop_hhmm[0], stop_hhmm[1], tzinfo=self._tz
        )

        if stop <= start:
            stop = stop + timedelta(days=1)
            if now < start:
                start = start - timedelta(days=1)

        return (start <= now < stop), start, stop

    @staticmethod
    def _bucket_key(outside_temp_c: float, wind_m_s: float | None) -> tuple[int, int | None]:
        t_bucket = int(round(outside_temp_c / 2.0) * 2)
        wind_bucket = int(round(wind_m_s / 2.0) * 2) if wind_m_s is not None else None
        return t_bucket, wind_bucket

    def _reset(self, reason: str) -> None:
        self._state = "IDLE"
        self._heater_on_streak = 0
        self._cooldown_until = None
        self._session_started_at = None
        self._session_window_date = None
        self._session_window_start = None
        self._session_window_stop = None
        self._session_flags = {"reason": reason}
        self._session_samples = []

    def _start_session(
        self,
        *,
        now: datetime,
        window_start: datetime,
        window_stop: datetime,
        cabin_temp_c: float,
        heater_on: bool,
        power_w: float,
        outside_temp_c: float,
        wind_m_s: float | None,
    ) -> None:
        self._state = "RECORDING"
        self._session_started_at = now
        self._session_window_date = window_start.date().isoformat()
        self._session_window_start = self._cfg.auto_calib_start_hhmm
        self._session_window_stop = self._cfg.auto_calib_stop_hhmm
        self._session_flags = {}
        self._session_samples = []
        self._append_sample(
            ts=now,
            cabin_temp_c=cabin_temp_c,
            heater_on=heater_on,
            power_w=power_w,
            outside_temp_c=outside_temp_c,
            wind_m_s=wind_m_s,
        )
        logger.info(
            "kfactor: session started (window=%s %s..%s, Tout=%.1fC)",
            self._session_window_date,
            self._session_window_start,
            self._session_window_stop,
            outside_temp_c,
        )

    def _append_sample(
        self,
        *,
        ts: datetime,
        cabin_temp_c: float,
        heater_on: bool,
        power_w: float,
        outside_temp_c: float,
        wind_m_s: float | None,
    ) -> None:
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

    def _session_duration_s(self, now: datetime) -> int | None:
        if self._session_started_at is None:
            return None
        return int((now - self._session_started_at).total_seconds())

    def _detect_disturbance(self) -> str | None:
        samples = self._session_samples
        if len(samples) < 3:
            return None

        # Spike outliers (sensor glitches)
        last = samples[-1]
        prev = samples[-2]
        dT = last.cabin_temp_c - prev.cabin_temp_c
        if abs(dT) >= float(self._cfg.spike_threshold_C):
            self._session_flags["spike_outlier"] = {"dT": dT, "threshold": self._cfg.spike_threshold_C}
            return "disturbance_spike"

        # Early non-monotonic rise check
        started = self._session_started_at
        if started is None:
            return None

        early_s = int(self._cfg.early_window_minutes) * 60
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

    def _finalize_session(self, *, end_ts: datetime, reason: str, is_test: bool) -> None:
        started = self._session_started_at
        samples = list(self._session_samples)

        self._state = "COOLDOWN"
        self._cooldown_until = end_ts + timedelta(minutes=int(self._cfg.cooldown_minutes))
        self._heater_on_streak = 0

        # Clear live session state early (even if we fail later).
        self._session_started_at = None
        self._session_samples = []

        if started is None or len(samples) < 2:
            self._last_session = KFactorLastSession(
                started_ts=_iso_no_micros(end_ts),
                ended_ts=_iso_no_micros(end_ts),
                sample_count=len(samples),
                accepted=False,
                quality_score=0.0,
                reason=reason,
                flags={"error": "empty_session"},
                fit=None,
            )
            return

        duration_s = int((end_ts - started).total_seconds())
        cabin_t_start = samples[0].cabin_temp_c
        cabin_t_end = samples[-1].cabin_temp_c
        cabin_t_max = max(s.cabin_temp_c for s in samples)
        delta_t = cabin_t_end - cabin_t_start

        outside_vals = [s.outside_temp_c for s in samples if math.isfinite(s.outside_temp_c)]
        wind_vals = [s.wind_m_s for s in samples if s.wind_m_s is not None and math.isfinite(s.wind_m_s)]
        outside_mean = mean(outside_vals) if outside_vals else None
        outside_min = min(outside_vals) if outside_vals else None
        outside_max = max(outside_vals) if outside_vals else None
        wind_mean = mean(wind_vals) if wind_vals else None

        flags: dict[str, Any] = dict(self._session_flags)
        flags["stop_reason"] = reason

        informative, info_details = self._is_informative_session(
            samples=samples,
            started=started,
            ended=end_ts,
        )
        if not informative:
            flags["not_informative"] = info_details
        fit = self._fit_params(samples) if informative else None
        rmse = fit.rmse_C if fit is not None else float("inf")

        quality = self._quality_score(
            duration_s=duration_s,
            delta_t=delta_t,
            flags=flags,
            rmse_C=rmse,
        )

        accepted = self._accept_session(
            duration_s=duration_s,
            delta_t=delta_t,
            flags=flags,
            quality_score=quality,
        )

        flags_json = json.dumps(flags, separators=(",", ":"), sort_keys=True)

        active_before_k, active_before_eta = self.get_active_params()
        promoted = False
        active_after: dict[str, Any] | None = None
        if fit is not None and accepted:
            new_k, new_eta, alpha = self._compute_weighted_update(
                old_k=active_before_k,
                old_eta=active_before_eta,
                fit=fit,
                quality_score=quality,
            )
            active_after = {
                "k_loss_W_per_K": new_k,
                "eta": new_eta,
                "alpha": alpha,
            }
            promoted = True
            # In test mode (no DB writes), keep params in memory so repeated runs behave realistically.
            if is_test or self._ctrl is None:
                self._active_params_override = (new_k, new_eta)

        self._last_session = KFactorLastSession(
            started_ts=_iso_no_micros(started),
            ended_ts=_iso_no_micros(end_ts),
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
                    "created_ts": _iso_no_micros(end_ts),
                    "session": {
                        "start_ts": _iso_no_micros(started),
                        "end_ts": _iso_no_micros(end_ts),
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

        # Persist session + result + active params (best-effort).
        try:
            if self._ctrl is None:
                return

            session_row = CarHeaterKFactorSession(
                id=None,
                start_ts=_iso_no_micros(started),
                end_ts=_iso_no_micros(end_ts),
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
                        fit=fit,
                        quality_score=quality,
                        updated_at=end_ts,
                    )

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
                    created_ts=_iso_no_micros(end_ts),
                )
                self._ctrl.record_kfactor_result(result_row)
        except Exception:
            logger.exception("kfactor: failed to persist calibration results")

    def _append_test_output(self, record: dict[str, Any]) -> None:
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

    def _compute_weighted_update(
        self,
        *,
        old_k: float,
        old_eta: float,
        fit: KFactorFit,
        quality_score: float,
    ) -> tuple[float, float, float]:
        alpha = _clamp(
            float(quality_score) * float(self._cfg.base_alpha),
            float(self._cfg.alpha_min),
            float(self._cfg.alpha_max),
        )
        new_k = (1.0 - alpha) * float(old_k) + alpha * float(fit.k_loss_W_per_K)
        new_eta = (1.0 - alpha) * float(old_eta) + alpha * float(fit.eta)
        return new_k, new_eta, alpha

    def _quality_score(
        self,
        *,
        duration_s: int,
        delta_t: float,
        flags: dict[str, Any],
        rmse_C: float,
    ) -> float:
        min_s = int(self._cfg.min_session_minutes) * 60
        ideal_s = max(min_s + 10 * 60, min_s * 2)
        if duration_s < min_s:
            duration_score = 0.0
        else:
            duration_score = _clamp((duration_s - min_s) / float(ideal_s - min_s), 0.0, 1.0)

        if delta_t <= 0:
            delta_score = 0.0
        else:
            delta_score = _clamp(delta_t / float(self._cfg.min_temp_rise_C * 2.0), 0.0, 1.0)

        disturbance = any(
            k.startswith("non_monotonic") or k.startswith("spike") for k in flags.keys()
        )
        smoothness_score = 0.0 if disturbance else 1.0

        informative_score = 0.0 if "not_informative" in flags else 1.0

        continuity_score = 1.0  # we only record continuous heater-on sessions

        if not math.isfinite(rmse_C):
            fit_score = 0.0
        else:
            fit_score = _clamp(1.0 - (rmse_C / 1.5), 0.0, 1.0)

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
        min_s = int(self._cfg.min_session_minutes) * 60
        if duration_s < min_s:
            return False
        if delta_t < float(self._cfg.min_temp_rise_C):
            return False
        if "non_monotonic_early" in flags or "spike_outlier" in flags:
            return False
        if "not_informative" in flags:
            return False
        return quality_score >= float(self._cfg.quality_threshold)

    def _is_informative_session(
        self,
        *,
        samples: list[KFactorSample],
        started: datetime,
        ended: datetime,
    ) -> tuple[bool, dict[str, Any]]:
        """Return (is_informative, details)."""
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
        """Return cabin temp for the sample closest in time to ts (with max distance guard)."""
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

    def _fit_params(self, samples: list[KFactorSample]) -> KFactorFit | None:
        if len(samples) < 3:
            return None

        # Build time/power series (ignore heater-off samples).
        series: list[tuple[datetime, float, float, float]] = []
        for s in samples:
            if not s.heater_on:
                continue
            series.append(
                (s.ts, float(s.cabin_temp_c), float(s.power_w), float(s.outside_temp_c))
            )

        if len(series) < 3:
            return None

        times = [t for (t, _, _, _) in series]
        obs = [v for (_, v, _, _) in series]
        powers = [p for (_, _, p, _) in series]
        tout = [x for (_, _, _, x) in series]
        tout = self._smooth_series(tout, window=max(1, int(self._cfg.tout_smooth_window)))

        k0, eta0 = self.get_active_params()
        k0 = _clamp(float(k0), self._cfg.k_loss_min, self._cfg.k_loss_max)
        eta0 = _clamp(float(eta0), self._cfg.eta_min, self._cfg.eta_max)

        # Two-step fitting to reduce k_loss/eta tradeoff on weak sessions:
        # 1) Fit k_loss with eta fixed to current active eta.
        # 2) Fit eta with k_loss fixed.
        best_k, best_rmse_k = self._fit_k_loss_only(
            times=times,
            obs=obs,
            powers=powers,
            tout=tout,
            k0=k0,
            eta_fixed=eta0,
        )
        best_eta, best_rmse = self._fit_eta_only(
            times=times,
            obs=obs,
            powers=powers,
            tout=tout,
            eta0=eta0,
            k_fixed=best_k,
        )

        # Optional: one small refinement pass around the two-step solution.
        best_k, best_eta, best_rmse = self._refine_joint(
            times=times,
            obs=obs,
            powers=powers,
            tout=tout,
            k0=k0,
            eta0=eta0,
            k_start=best_k,
            eta_start=best_eta,
        )

        r2 = self._r2_for(times, obs, powers, tout=tout, k_loss=best_k, eta=best_eta)
        return KFactorFit(k_loss_W_per_K=float(best_k), eta=float(best_eta), rmse_C=float(best_rmse), r2=r2)

    def _fit_k_loss_only(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        k0: float,
        eta_fixed: float,
    ) -> tuple[float, float]:
        best_k = _clamp(float(k0), self._cfg.k_loss_min, self._cfg.k_loss_max)
        best_rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=best_k, eta=eta_fixed)
        best_loss = best_rmse

        stages = [
            (0.3, 3.0, 61),
            (0.7, 1.3, 41),
            (0.85, 1.15, 31),
        ]
        lam_k = float(self._cfg.prior_lambda_k)
        for lo_fac, hi_fac, n in stages:
            k_min = _clamp(best_k * lo_fac, self._cfg.k_loss_min, self._cfg.k_loss_max)
            k_max = _clamp(best_k * hi_fac, self._cfg.k_loss_min, self._cfg.k_loss_max)
            for k in self._linspace(k_min, k_max, n):
                rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=k, eta=eta_fixed)
                if not math.isfinite(rmse):
                    continue
                loss = rmse + (lam_k * abs(k - k0) / max(1e-9, abs(k0)))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_k = k

        return float(best_k), float(best_rmse)

    def _fit_eta_only(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        eta0: float,
        k_fixed: float,
    ) -> tuple[float, float]:
        best_eta = _clamp(float(eta0), self._cfg.eta_min, self._cfg.eta_max)
        best_rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=k_fixed, eta=best_eta)
        best_loss = best_rmse

        stages = [
            (0.5, 51),
            (0.25, 41),
            (0.15, 31),
        ]
        lam_e = float(self._cfg.prior_lambda_eta)
        for span, n in stages:
            eta_min = _clamp(best_eta - span, self._cfg.eta_min, self._cfg.eta_max)
            eta_max = _clamp(best_eta + span, self._cfg.eta_min, self._cfg.eta_max)
            for eta in self._linspace(eta_min, eta_max, n):
                rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=k_fixed, eta=eta)
                if not math.isfinite(rmse):
                    continue
                loss = rmse + (lam_e * abs(eta - eta0))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_eta = eta

        return float(best_eta), float(best_rmse)

    def _refine_joint(
        self,
        *,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        tout: list[float],
        k0: float,
        eta0: float,
        k_start: float,
        eta_start: float,
    ) -> tuple[float, float, float]:
        best_k = _clamp(float(k_start), self._cfg.k_loss_min, self._cfg.k_loss_max)
        best_eta = _clamp(float(eta_start), self._cfg.eta_min, self._cfg.eta_max)
        best_rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=best_k, eta=best_eta)
        best_loss = best_rmse

        lam_k = float(self._cfg.prior_lambda_k)
        lam_e = float(self._cfg.prior_lambda_eta)

        k_min = _clamp(best_k * 0.85, self._cfg.k_loss_min, self._cfg.k_loss_max)
        k_max = _clamp(best_k * 1.15, self._cfg.k_loss_min, self._cfg.k_loss_max)
        eta_min = _clamp(best_eta - 0.15, self._cfg.eta_min, self._cfg.eta_max)
        eta_max = _clamp(best_eta + 0.15, self._cfg.eta_min, self._cfg.eta_max)

        for k in self._linspace(k_min, k_max, 13):
            for eta in self._linspace(eta_min, eta_max, 13):
                rmse = self._rmse_for(times, obs, powers, tout=tout, k_loss=k, eta=eta)
                if not math.isfinite(rmse):
                    continue
                loss = rmse
                loss += (lam_k * abs(k - k0) / max(1e-9, abs(k0)))
                loss += (lam_e * abs(eta - eta0))
                if loss < best_loss:
                    best_loss = loss
                    best_rmse = rmse
                    best_k = k
                    best_eta = eta

        return float(best_k), float(best_eta), float(best_rmse)

    @staticmethod
    def _linspace(lo: float, hi: float, n: int) -> list[float]:
        if n <= 1:
            return [float(lo)]
        step = (hi - lo) / float(n - 1)
        return [float(lo + i * step) for i in range(n)]

    def _rmse_for(
        self,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> float:
        pred = self._simulate(times, obs[0], powers, tout=tout, k_loss=k_loss, eta=eta)
        if pred is None:
            return float("inf")
        err2 = 0.0
        n = 0
        for y_hat, y in zip(pred, obs):
            if not (math.isfinite(y_hat) and math.isfinite(y)):
                continue
            d = y_hat - y
            err2 += d * d
            n += 1
        return math.sqrt(err2 / n) if n else float("inf")

    def _r2_for(
        self,
        times: list[datetime],
        obs: list[float],
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> float | None:
        pred = self._simulate(times, obs[0], powers, tout=tout, k_loss=k_loss, eta=eta)
        if pred is None:
            return None
        y_bar = mean(obs)
        sse = 0.0
        sst = 0.0
        for y_hat, y in zip(pred, obs):
            if not (math.isfinite(y_hat) and math.isfinite(y)):
                continue
            d = y - y_hat
            sse += d * d
            v = y - y_bar
            sst += v * v
        if sst <= 0.0:
            return None
        return 1.0 - (sse / sst)

    def _simulate(
        self,
        times: list[datetime],
        T0: float,
        powers: list[float],
        *,
        tout: list[float],
        k_loss: float,
        eta: float,
    ) -> list[float] | None:
        if k_loss <= 0.0 or not math.isfinite(k_loss):
            return None
        if not (self._cfg.eta_min <= eta <= self._cfg.eta_max) or not math.isfinite(eta):
            return None
        if len(tout) != len(times):
            return None

        T = float(T0)
        out: list[float] = [T]
        C_eff = HEAT_CAPACITY_J_PER_K * _clamp(
            float(self._cfg.mass_factor),
            float(self._cfg.mass_factor_min),
            float(self._cfg.mass_factor_max),
        )

        for i in range(1, len(times)):
            dt_s = (times[i] - times[i - 1]).total_seconds()
            if dt_s <= 0:
                out.append(T)
                continue

            P = powers[i - 1] if i - 1 < len(powers) else powers[-1]
            P = float(P) if (P is not None and math.isfinite(P)) else HEATER_POWER_W
            P = P if P > 1.0 else HEATER_POWER_W

            Tout_i = float(tout[i - 1])
            Tss = Tout_i + (eta * P) / k_loss
            decay = math.exp(-(k_loss / C_eff) * dt_s)
            T = Tss + (T - Tss) * decay
            out.append(T)
        return out

    @staticmethod
    def _smooth_series(values: list[float], *, window: int) -> list[float]:
        """Simple moving average smoothing (trailing window)."""
        if window <= 1 or len(values) < 2:
            return list(values)
        out: list[float] = []
        acc: list[float] = []
        for v in values:
            acc.append(v)
            if len(acc) > window:
                acc.pop(0)
            out.append(float(mean(acc)))
        return out

    def _update_active_params(self, *, fit: KFactorFit, quality_score: float, updated_at: datetime) -> bool:
        if self._ctrl is None:
            return False

        old_k, old_eta = self.get_active_params()
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
            updated_ts=_iso_no_micros(updated_at),
            source="weighted_update",
        )
        self._ctrl.save_kfactor_active_params(params)
        self._active_params_override = (float(new_k), float(new_eta))
        return True
