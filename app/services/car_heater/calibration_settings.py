"""Calibration Settings: the owner-facing contract shared by HTTP and Socket.IO.

``calibration_payload`` is what every view shows, and ``apply_calibration_changes``
is the single save path, so validation, bounded-session ownership and the
persisted result are identical on both transports. Calibration is experimental:
it tunes the cabin-air model used by comfort and legacy Ready-by and never
controls departure heating.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .car_heater_service import CarHeaterService
    from .kfactor import KFactorCalibrator
    from .ready_by_service import ReadyByService

logger = logging.getLogger(__name__)

OWNERSHIP_REASON = "Stop heating and wait for confirmed OFF before starting another mode."
NOT_SAVED = "Not all calibration settings were saved. The values shown are what the server kept."


class CalibrationRefused(ValueError):
    """A valid request the current heater ownership does not allow."""


# Owner-editable model settings: (type, minimum, maximum, label). The bounds
# match the Settings page; autonomous-session internals and the test output
# path stay server-owned.
KFACTOR_FIELDS: dict[str, tuple[type, float, float, str]] = {
    "grace_samples": (int, 1, 10, "Grace samples"),
    "min_session_minutes": (int, 5, 60, "Minimum session duration"),
    "max_session_minutes": (int, 30, 240, "Maximum session duration"),
    "min_temp_rise_C": (float, 0.5, 10, "Minimum temperature rise"),
    "cooldown_minutes": (int, 15, 480, "Cooldown"),
    "bucket_lookback_days": (int, 1, 30, "Bucket lookback"),
    "quality_threshold": (float, 0.1, 1.0, "Quality threshold"),
    "early_window_minutes": (int, 1, 15, "Early window"),
    "drop_threshold_C": (float, 0.1, 2.0, "Drop threshold"),
    "spike_threshold_C": (float, 1, 15, "Spike threshold"),
    "default_eta": (float, 0.1, 1.0, "Default efficiency (η)"),
    "default_k_loss_W_per_K": (float, 10, 200, "Default heat loss (k_loss)"),
    "mass_factor": (float, 5, 150, "Mass factor"),
    "eta_min": (float, 0.01, 0.5, "η minimum"),
    "eta_max": (float, 0.5, 1.0, "η maximum"),
    "k_loss_min": (float, 1, 50, "k_loss minimum"),
    "k_loss_max": (float, 50, 1000, "k_loss maximum"),
    "mass_factor_min": (float, 1, 20, "Mass factor minimum"),
    "mass_factor_max": (float, 50, 500, "Mass factor maximum"),
    "tout_smooth_window": (int, 1, 10, "Outside temperature smoothing"),
    "informative_min_minutes": (int, 5, 60, "Informative minimum"),
    "curvature_ratio_max": (float, 0.1, 1.0, "Curvature ratio maximum"),
    "curvature_max_sample_distance_s": (int, 30, 300, "Curvature sample spacing"),
    "prior_lambda_k": (float, 0, 1, "Prior strength for k_loss"),
    "prior_lambda_eta": (float, 0, 1, "Prior strength for η"),
    "base_alpha": (float, 0.05, 1.0, "Base update rate (alpha)"),
    "alpha_min": (float, 0.01, 0.5, "Minimum alpha"),
    "alpha_max": (float, 0.1, 1.0, "Maximum alpha"),
}
WINDOW_FIELDS = {
    "auto_calib_start_hhmm": "Calibration window start",
    "auto_calib_stop_hhmm": "Calibration window end",
}
READY_BY_FIELDS: dict[str, tuple[type, float, float, str]] = {
    "reach_tolerance_minutes": (float, 0, 30, "Reach tolerance"),
    "command_cooldown_s": (int, 5, 300, "Command cooldown"),
}
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

# The collapsed advanced controls: (title, explanation, expert, [(field, unit, help)]).
ADVANCED_GROUPS: list[tuple[str, str, bool, list[tuple[str, str, str]]]] = [
    (
        "Session rules",
        "When a recorded heating session starts, how long it may run and when it counts.",
        False,
        [
            (
                "grace_samples",
                "reports",
                "Consecutive heater-on reports before recording starts, "
                "so a brief switch-on is ignored.",
            ),
            ("min_session_minutes", "min", "Shorter recordings are rejected as too little data."),
            ("max_session_minutes", "min", "A passive recording is finished after this long."),
            (
                "min_temp_rise_C",
                "°C",
                "The cabin must warm at least this much for the session to count.",
            ),
            ("cooldown_minutes", "min", "Wait after a passive session before recording another."),
            (
                "bucket_lookback_days",
                "days",
                "How far back a temperature and wind bucket counts as already calibrated.",
            ),
        ],
    ),
    (
        "Quality and acceptance",
        "Whether a recorded session is clean enough to update the model.",
        False,
        [
            (
                "quality_threshold",
                "0\u20131",
                "Minimum quality score to accept a fit. Higher is stricter.",
            ),
            (
                "early_window_minutes",
                "min",
                "Early part of a session checked for disturbances such as an opened door.",
            ),
            ("drop_threshold_C", "°C", "A cabin drop this large counts as a disturbance."),
            ("spike_threshold_C", "°C", "A jump this large is treated as a sensor glitch."),
        ],
    ),
    (
        "Model defaults",
        "Used where no calibration exists for the current outside conditions.",
        False,
        [
            ("default_eta", "0\u20131", "Share of heater power that warms the cabin."),
            (
                "default_k_loss_W_per_K",
                "W/K",
                "Heat lost per degree between cabin and outside. "
                "Higher means the cabin cools faster.",
            ),
            (
                "mass_factor",
                "\u00d7",
                "Multiplies the air's heat capacity to account for seats, dashboard and glass.",
            ),
        ],
    ),
    (
        "Parameter bounds",
        "Hard limits for fitted values, so noisy data cannot produce unrealistic results.",
        True,
        [
            ("eta_min", "0\u20131", "Lowest efficiency a fit may return."),
            ("eta_max", "0\u20131", "Highest efficiency a fit may return."),
            ("k_loss_min", "W/K", "Lowest heat loss a fit may return."),
            ("k_loss_max", "W/K", "Highest heat loss a fit may return."),
            ("mass_factor_min", "\u00d7", "Lowest mass factor a fit may return."),
            ("mass_factor_max", "\u00d7", "Highest mass factor a fit may return."),
        ],
    ),
    (
        "Fitting",
        "How the curve fit smooths inputs and decides a session is informative.",
        True,
        [
            ("tout_smooth_window", "reports", "Outside temperature readings averaged together."),
            (
                "informative_min_minutes",
                "min",
                "Minimum length before the informativeness check applies.",
            ),
            (
                "curvature_ratio_max",
                "0\u20131",
                "Largest allowed curvature ratio. Lower requires a more curved warm-up.",
            ),
            (
                "curvature_max_sample_distance_s",
                "s",
                "Largest gap between readings used for the curvature check.",
            ),
        ],
    ),
    (
        "Priors and smoothing",
        "How strongly fits lean on the defaults and how fast accepted fits change the model.",
        True,
        [
            (
                "prior_lambda_k",
                "0\u20131",
                "Pulls fitted heat loss toward the default. 0 turns it off.",
            ),
            (
                "prior_lambda_eta",
                "0\u20131",
                "Pulls fitted efficiency toward the default. 0 turns it off.",
            ),
            (
                "base_alpha",
                "0\u20131",
                "How much one accepted session moves the active parameters.",
            ),
            ("alpha_min", "0\u20131", "Slowest allowed update rate."),
            ("alpha_max", "0\u20131", "Fastest allowed update rate."),
        ],
    ),
]


def settings_form_groups() -> list[dict[str, Any]]:
    """Advanced fields for the Settings template, with the bounds the server enforces."""
    return [
        {
            "title": title,
            "help": explanation,
            "expert": expert,
            "fields": [
                {
                    "key": key,
                    "label": KFACTOR_FIELDS[key][3],
                    "min": KFACTOR_FIELDS[key][1],
                    "max": KFACTOR_FIELDS[key][2],
                    "step": 1 if KFACTOR_FIELDS[key][0] is int else "any",
                    "unit": unit,
                    "help": help_text,
                }
                for key, unit, help_text in fields
            ],
        }
        for title, explanation, expert, fields in ADVANCED_GROUPS
    ]


def _ownership_reason(heater: CarHeaterService | None) -> str | None:
    if heater is None:
        return None
    try:
        heater.require_mode_available()
    except ValueError:
        return OWNERSHIP_REASON
    return None


def calibration_payload(
    kfactor: KFactorCalibrator,
    ready_by: ReadyByService | None,
    heater: CarHeaterService | None,
) -> dict[str, Any]:
    """Saved calibration settings, model status and what can start now."""
    snapshot = kfactor.get_debug_snapshot()
    config = snapshot.get("config", {})
    reason = _ownership_reason(heater)
    return {
        "experimental": True,
        "state": snapshot.get("state"),
        "autonomous_enabled": bool(config.get("enabled", False)),
        "autonomous_start_available": reason is None,
        "unavailable_reason": reason,
        "autonomous_waiting_for": kfactor.autonomous_conflict(),
        "active_params": snapshot.get("active_params"),
        "last_session": snapshot.get("last_session"),
        "live_session": snapshot.get("live_session"),
        "autonomous_cooldown_until": snapshot.get("autonomous_cooldown_until"),
        "passive_cooldown_until": snapshot.get("passive_cooldown_until"),
        "model_version": config.get("model_version"),
        "config_saved_at": kfactor.config_updated_at,
        "config": {
            key: config.get(key) for key in (*WINDOW_FIELDS, *KFACTOR_FIELDS) if key in config
        },
        "ready_by": (
            {
                "enabled": ready_by.config.enabled,
                "reach_tolerance_minutes": ready_by.config.reach_tolerance_minutes,
                "command_cooldown_s": ready_by.config.command_cooldown_s,
            }
            if ready_by is not None
            else None
        ),
    }


def _number(label: str, value: Any, spec: tuple[type, float, float, str]) -> int | float:
    kind, low, high, _ = spec
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a number")
    if kind is int and value != int(value):
        raise ValueError(f"{label} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}")
    return int(value) if kind is int else float(value)


def _validated(section: Any, fields: dict, name: str) -> dict[str, Any]:
    if not isinstance(section, dict):
        raise ValueError(f"{name} must be an object of fields to save")
    unknown = sorted(set(section) - set(fields))
    if unknown:
        raise ValueError(f"These {name} fields cannot be changed here: {', '.join(unknown)}")
    return section


# Lower limits that must not exceed their upper counterpart once merged.
ORDERED_LIMITS = [
    ("min_session_minutes", "max_session_minutes"),
    ("eta_min", "eta_max"),
    ("k_loss_min", "k_loss_max"),
    ("mass_factor_min", "mass_factor_max"),
    ("alpha_min", "alpha_max"),
]


def _check_ordered_limits(merged: dict[str, Any]) -> None:
    for low, high in ORDERED_LIMITS:
        if merged[low] > merged[high]:
            raise ValueError(
                f"{KFACTOR_FIELDS[low][3]} must not be greater than {KFACTOR_FIELDS[high][3]}"
            )


def validate_changes(data: Any) -> dict[str, Any]:
    """Check a partial change set without saving anything; returns clean values."""
    if not isinstance(data, dict) or not data:
        raise ValueError("Expected a JSON object with the fields to save")
    unknown = sorted(set(data) - {"autonomous_enabled", "config", "ready_by"})
    if unknown:
        raise ValueError(f"Unknown calibration fields: {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    if "autonomous_enabled" in data:
        if not isinstance(data["autonomous_enabled"], bool):
            raise ValueError("Autonomous calibration must be on or off")
        clean["autonomous_enabled"] = data["autonomous_enabled"]
    if "config" in data:
        section = _validated(data["config"], {**KFACTOR_FIELDS, **WINDOW_FIELDS}, "calibration")
        config: dict[str, Any] = {}
        for key, value in section.items():
            if key in WINDOW_FIELDS:
                if not isinstance(value, str) or not _HHMM.match(value):
                    raise ValueError(f"{WINDOW_FIELDS[key]} must be a 24-hour time (HH:MM)")
                config[key] = value
            else:
                config[key] = _number(KFACTOR_FIELDS[key][3], value, KFACTOR_FIELDS[key])
        clean["config"] = config
    if "ready_by" in data:
        section = _validated(data["ready_by"], {**READY_BY_FIELDS, "enabled": None}, "Ready-by")
        ready_by: dict[str, Any] = {}
        for key, value in section.items():
            if key == "enabled":
                if not isinstance(value, bool):
                    raise ValueError("Ready-by scheduling must be on or off")
                ready_by[key] = value
            else:
                ready_by[key] = _number(READY_BY_FIELDS[key][3], value, READY_BY_FIELDS[key])
        clean["ready_by"] = ready_by
    return clean


def apply_calibration_changes(
    kfactor: KFactorCalibrator,
    ready_by: ReadyByService | None,
    heater: CarHeaterService | None,
    data: Any,
) -> None:
    """Validate everything, check ownership, then save; absent fields keep their values.

    Raises ``CalibrationRefused`` when an active bounded session owns the heater,
    ``ValueError`` for invalid input, and any persistence error unchanged.
    """
    clean = validate_changes(data)
    if "ready_by" in clean and ready_by is None:
        raise ValueError("Ready-by settings are unavailable")
    if clean.get("autonomous_enabled"):
        reason = _ownership_reason(heater)
        if reason:
            logger.warning("Autonomous calibration refused during a bounded session")
            raise CalibrationRefused(reason)
    changes = dict(clean.get("config", {}))
    _check_ordered_limits({**asdict(kfactor.config), **changes})
    if "autonomous_enabled" in clean:
        changes["enabled"] = clean["autonomous_enabled"]
    if changes:
        try:
            kfactor.save_settings(changes)
        except ValueError as exc:
            # The heater re-checks ownership under its own lock.
            if changes.get("enabled") and _ownership_reason(heater):
                raise CalibrationRefused(OWNERSHIP_REASON) from exc
            raise
    if clean.get("ready_by"):
        ready_by.save_settings(clean["ready_by"])


CALIBRATION_ACTIONS = ("reset_cooldown", "reset_defaults")


def run_calibration_action(kfactor: KFactorCalibrator, action: str) -> None:
    """Clear cooldowns or restore default settings; neither can start heating.

    Defaults keep autonomous calibration off, so a reset ends any autonomous
    session through the normal disable path.
    """
    if action == "reset_cooldown":
        kfactor.reset_cooldown(reason="owner_reset_cooldown")
    elif action == "reset_defaults":
        from .kfactor import KFactorConfig

        kfactor.save_settings(asdict(KFactorConfig()))
    else:
        raise ValueError(f"Unknown calibration action: {action}")
    logger.info("Calibration action %s completed", action)


def calibration_data(kfactor: KFactorCalibrator) -> dict[str, Any]:
    """Recorded calibration sessions, bucket coverage and statistics."""
    extended = kfactor.get_extended_snapshot()
    return {
        "live_session": extended.get("live_session"),
        "recent_sessions": extended.get("recent_sessions", []),
        "bucket_coverage": extended.get("bucket_coverage", []),
        "statistics": extended.get("statistics", {}),
    }
