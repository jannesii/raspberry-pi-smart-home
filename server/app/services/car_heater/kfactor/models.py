"""
KFactor configuration and data models.

Dataclasses for configuration and session data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class KFactorConfig:
    """Configuration for KFactor calibration."""

    # --- General Settings ---
    enabled: bool = False  # Autonomous mode (actively turns heater on/off)
    auto_calib_start_hhmm: str = "00:00"  # Autonomous window start
    auto_calib_stop_hhmm: str = "23:59"   # Autonomous window stop
    grace_samples: int = 3
    min_session_minutes: int = 15
    max_session_minutes: int = 120  # For passive recording
    min_temp_rise_C: float = 2.0
    early_window_minutes: int = 5
    drop_threshold_C: float = 0.5
    spike_threshold_C: float = 5.0
    quality_threshold: float = 0.6
    cooldown_minutes: int = 120  # For passive recording
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

    # Test-mode output path
    test_output_path: str = "/home/jannesi/Code/server/app/services/car_heater/car_heater_kfactor_test_results.jsonl"
    model_version: str = "v1"

    # --- Autonomous Mode Settings ---
    autonomous_max_session_minutes: int = 45  # Shorter sessions for autonomous
    # Shorter cooldown (survives reboots)
    autonomous_cooldown_minutes: int = 60
    autonomous_min_temp_rise_rate_C_per_min: float = 0.08  # Stop if heating too slow
    autonomous_rise_rate_window_samples: int = 5  # Samples to average for rise rate
    # Min consecutive slow readings before stopping
    autonomous_rise_rate_min_checks: int = 3
    autonomous_target_temp_c: float = 10.0    # Stop when cabin reaches this
    # Alias for autonomous_rise_rate_min_checks
    autonomous_slow_rise_samples: int = 3


@dataclass(frozen=True)
class KFactorSample:
    """A single calibration sample."""
    ts: datetime
    cabin_temp_c: float
    heater_on: bool
    power_w: float
    outside_temp_c: float
    wind_m_s: float | None = None


@dataclass(frozen=True)
class KFactorFit:
    """Result of parameter fitting."""
    k_loss_W_per_K: float
    eta: float
    rmse_C: float
    r2: float | None


@dataclass(frozen=True)
class KFactorLastSession:
    """Summary of the last calibration session."""
    started_ts: str
    ended_ts: str
    sample_count: int
    accepted: bool
    quality_score: float
    reason: str
    flags: dict[str, Any]
    fit: KFactorFit | None = None
