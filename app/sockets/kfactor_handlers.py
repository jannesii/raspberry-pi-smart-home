"""
KFactor calibration socket event handlers.

All functions receive the SocketEventHandler instance as the first argument.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from flask import current_app
from flask_login import current_user

from ..services.car_heater.calibration_settings import (
    NOT_SAVED,
    apply_calibration_changes,
    run_calibration_action,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..services.car_heater import KFactorCalibrator
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def authorized_owner(handler: SocketEventHandler, data: dict[str, Any]) -> bool:
    """Browser mutations need a signed-in administrator and a valid CSRF token."""
    from flask_wtf.csrf import ValidationError, validate_csrf

    try:
        if not current_user.is_authenticated:
            raise ValueError("Authentication required")
        validate_csrf(data.get("csrf_token"))
    except (ValidationError, ValueError) as exc:
        logger.warning("Calibration socket change refused: %s", exc)
        handler.socketio.emit("error", {"message": str(exc)})
        return False
    if not getattr(current_user, "is_admin", False):
        logger.warning("Non-admin %s refused calibration change", current_user.get_id())
        handler.flash("Only administrators can change calibration.", "error")
        return False
    return True


def save_calibration(handler: SocketEventHandler, save: Callable[..., None]) -> None:
    """Run one shared save and send every view the persisted result, also on refusal."""
    from ..services.car_heater.calibration_settings import calibration_payload

    kfactor = getattr(current_app, "kfactor_calibrator", None)
    ready_by = getattr(current_app, "ready_by_service", None)
    heater = getattr(current_app, "car_heater_service", None)
    if kfactor is None:
        handler.socketio.emit("error", {"message": "Calibration is unavailable"})
        return
    try:
        save(kfactor, ready_by, heater)
    except ValueError as exc:
        # Includes ownership refusals; the saved state lets the page drop its edit.
        logger.warning("Calibration change refused: %s", exc)
        handler.socketio.emit("error", {"message": str(exc)})
    except Exception:
        logger.exception("Calibration change via socket failed")
        handler.socketio.emit("error", {"message": NOT_SAVED})
    handler.emit_to_views("calibration_settings", calibration_payload(kfactor, ready_by, heater))


def emit_kfactor_status(handler: SocketEventHandler, svc: KFactorCalibrator) -> None:
    """Helper to emit kFactor status to all views."""
    logger.debug("emit_kfactor_status called svc=%s", svc)
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
            "live_session": snapshot.get("live_session"),
            "config": snapshot.get("config", {}),
        },
    )


def handle_kfactor_control(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Handle kFactor calibration control from the web UI."""
    logger.debug("handle_kfactor_control called data=%s", data)
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
    logger.debug("handle_kfactor_control action=%s payload_keys=%s", action, list(data.keys()))

    if action in _MUTATIONS:
        if not authorized_owner(handler, data):
            return
        save_calibration(handler, _calibration_save(action, data))
        return

    try:
        if action == "status":
            emit_kfactor_status(handler, svc)

        elif action == "get_config":
            # Return full config for populating the UI
            config = asdict(svc.config)
            handler.emit_to_views("kfactor_config", {"config": config})

        elif action == "extended_data":
            # Return extended snapshot with live session, history, bucket coverage, stats
            extended = svc.get_extended_snapshot()
            handler.socketio.emit("kfactor_extended_data", extended)

        else:
            handler.socketio.emit("error", {"message": f"Invalid kfactor action: {action}"})

    except Exception as e:
        logger.exception("kfactor_control error: %s", e)
        handler.socketio.emit("error", {"message": "KFactor control error"})


_MUTATIONS = ("set_enabled", "update_config", "reset_cooldown", "reset_defaults")


def _calibration_save(action: str, data: dict[str, Any]) -> Callable[..., None]:
    """The shared HTTP contract for one browser mutation."""
    if action in ("reset_cooldown", "reset_defaults"):
        return lambda kfactor, _ready_by, _heater: run_calibration_action(kfactor, action)
    if action == "set_enabled":
        changes = {"autonomous_enabled": data.get("enabled")}
    else:
        changes = {"config": data.get("config")}
    return lambda kfactor, ready_by, heater: apply_calibration_changes(
        kfactor, ready_by, heater, changes
    )
