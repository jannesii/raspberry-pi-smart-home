"""
KFactor calibration socket event handlers.

All functions receive the SocketEventHandler instance as the first argument.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from flask import current_app

if TYPE_CHECKING:
    from ..services.car_heater import KFactorCalibrator
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def emit_kfactor_status(handler: SocketEventHandler, svc: KFactorCalibrator) -> None:
    """Helper to emit kFactor status to all views."""
    snapshot = svc.get_debug_snapshot()
    handler.emit_to_views(
        "kfactor_status",
        {
            "state": snapshot.get("state"),
            "autonomous_enabled": snapshot.get("autonomous_enabled", False),
            "is_autonomous_session": snapshot.get("is_autonomous_session", False),
            "autonomous_cooldown_until": snapshot.get("autonomous_cooldown_until"),
            "passive_cooldown_until": snapshot.get("passive_cooldown_until"),
            "active_params": snapshot.get("active_params"),
            "last_session": snapshot.get("last_session"),
            "session_sample_count": snapshot.get("session_sample_count", 0),
            "config": snapshot.get("config", {}),
        },
    )


def handle_kfactor_control(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Handle kFactor calibration control from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid kfactor payload"})
        logger.warning("Bad kfactor_control payload: %s", data)
        return

    try:
        svc: KFactorCalibrator | None = getattr(current_app, "kfactor_calibrator", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "KFactor service not initialized"})
        return

    action = (data.get("action") or "").strip()

    try:
        if action == "set_enabled":
            enabled = bool(data.get("enabled", True))
            svc.enabled = enabled
            logger.info("KFactor enabled set to %s via socket", enabled)
            emit_kfactor_status(handler, svc)

        elif action == "status":
            emit_kfactor_status(handler, svc)

        elif action == "get_config":
            # Return full config for populating the UI
            config = asdict(svc.config)
            handler.emit_to_views("kfactor_config", {"config": config})

        elif action == "update_config":
            _handle_update_config(handler, svc, data)

        elif action == "reset_defaults":
            _handle_reset_defaults(handler, svc)

        elif action == "extended_data":
            # Return extended snapshot with live session, history, bucket coverage, stats
            extended = svc.get_extended_snapshot()
            handler.socketio.emit("kfactor_extended_data", extended)

        else:
            handler.socketio.emit("error", {"message": f"Invalid kfactor action: {action}"})

    except Exception as e:
        logger.exception("kfactor_control error: %s", e)
        handler.socketio.emit("error", {"message": "KFactor control error"})


def _handle_update_config(
    handler: SocketEventHandler, svc: KFactorCalibrator, data: dict[str, Any]
) -> None:
    """Update config fields from the UI."""
    logger.debug("_handle_update_config called keys=%s", list(data.keys()))
    updates = data.get("config", {})
    if not isinstance(updates, dict):
        handler.socketio.emit("error", {"message": "Invalid config payload"})
        return

    # Get current config and merge updates
    current_config = asdict(svc.config)
    for key, value in updates.items():
        if key in current_config:
            # Type coercion based on current type
            current_type = type(current_config[key])
            try:
                if current_type is bool:
                    current_config[key] = bool(value)
                elif current_type is int:
                    current_config[key] = int(value)
                elif current_type is float:
                    current_config[key] = float(value)
                elif current_type is str:
                    current_config[key] = str(value)
                else:
                    current_config[key] = value
            except (TypeError, ValueError) as e:
                logger.warning("kfactor config: invalid value for %s: %s", key, e)
                continue

    # Create new config and save
    from ..services.car_heater import KFactorConfig

    new_config = KFactorConfig(**current_config)
    svc._cfg = new_config
    svc._save_config_in_db(new_config)

    logger.info("KFactor config updated via socket: %s", list(updates.keys()))

    # Emit updated config back to all views
    handler.emit_to_views(
        "kfactor_config",
        {
            "config": asdict(new_config),
            "saved": True,
        },
    )


def _handle_reset_defaults(handler: SocketEventHandler, svc: KFactorCalibrator) -> None:
    """Reset to default config."""
    from ..services.car_heater import KFactorConfig

    default_config = KFactorConfig()
    svc._cfg = default_config
    svc._save_config_in_db(default_config)

    logger.info("KFactor config reset to defaults via socket")

    handler.emit_to_views(
        "kfactor_config",
        {
            "config": asdict(default_config),
            "saved": True,
            "reset": True,
        },
    )
