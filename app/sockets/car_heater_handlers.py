"""
Car heater socket event handlers.

Includes: control, charge mode, keep-at-temp, and status emission.
All functions receive the SocketEventHandler instance as the first argument.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import current_app
from flask_login import current_user

if TYPE_CHECKING:
    from ..services.car_heater import CarHeaterService, KeepAtTempService
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def emit_car_heater_status_to_views(
    handler: SocketEventHandler,
    status_payload: dict[str, Any],
    command_status: dict[str, Any] | None,
    charge_mode_state: dict[str, Any] | None,
) -> None:
    """Emit car heater status to connected browser views via Socket.IO."""
    try:
        payload: dict[str, Any] = {"status": status_payload}
        if command_status is not None:
            payload["command_status"] = command_status
        if charge_mode_state is not None:
            payload["charge_mode"] = charge_mode_state

        # Include weather data
        try:
            weather_svc = getattr(current_app, "weather_service", None)
            if weather_svc:
                from ..services.weather import browser_conditions

                conditions = browser_conditions(weather_svc.get_latest())
                if conditions:
                    payload["weather"] = conditions
        except Exception:
            # Weather data is optional; the status is still emitted.
            logger.debug("Weather unavailable for car_heater_status emit", exc_info=True)

        handler.emit("car_heater_status", payload)
    except Exception as e:
        logger.exception("Failed to emit car_heater_status over Socket.IO: %s", e)


def handle_car_heater_control(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """
    Handle car heater actions from the web UI.

    Actions are queued via CarHeaterService; the ESP will fetch them
    on the next /car_heater/status POST.
    """
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid car heater payload"})
        logger.warning("Bad car_heater_control payload: %s", data)
        return

    action = (data.get("action") or "").strip()
    if not action:
        handler.socketio.emit("error", {"message": "Missing car heater action"})
        return

    try:
        svc: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Car heater service not initialized"})
        return

    from flask_wtf.csrf import ValidationError, validate_csrf

    try:
        if not current_user.is_authenticated:
            raise ValueError("Authentication required")
        validate_csrf(data.get("csrf_token"))
    except (ValidationError, ValueError) as exc:
        handler.socketio.emit("error", {"message": str(exc)})
        return

    try:
        # Get username for logging
        username = "unknown"
        if current_user and current_user.is_authenticated:
            username = current_user.get_id() or "unknown"
        logger.debug("handle_car_heater_control action=%s user=%s", action, username)

        # Use centralized turn_on/turn_off methods
        if action == "turn_on":
            svc.start_bounded_session(data.get("duration_minutes", 60))
        elif action == "turn_off":
            svc.turn_off(source="web_ui", reason=f"Manual control by {username}")
        else:
            # Other commands (get_logs, esp_restart, etc.)
            svc.queue_command({"action": action})

        handler.emit_to_views(
            "car_heater_action_result",
            {
                "ok": True,
                "action": action,
                "stage": "queued",
                "label": f"Queued: {action}",
            },
        )
    except ValueError as exc:
        handler.socketio.emit("error", {"message": str(exc)})
    except Exception as e:
        logger.exception("car_heater_control error: %s", e)
        handler.socketio.emit("error", {"message": "Car heater control error"})


def handle_car_heater_charge_mode(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Toggle car heater battery charge mode from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid car heater charge payload"})
        logger.warning("Bad car_heater_charge_mode payload: %s", data)
        return

    if "enabled" not in data:
        handler.socketio.emit("error", {"message": "Missing enabled flag for charge mode"})
        return

    try:
        svc: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Car heater service not initialized"})
        return

    try:
        state = svc.set_charge_mode_enabled(bool(data.get("enabled")))
        payload = {
            "enabled": state.enabled,
            "threshold_w": state.threshold_w,
            "power_cut": state.power_cut,
            "power_cut_at": state.power_cut_at,
            "last_instant_power_w": state.last_instant_power_w,
        }
        handler.emit_to_views("car_heater_charge_mode", payload)
    except Exception as e:
        logger.exception("car_heater_charge_mode error: %s", e)
        handler.socketio.emit("error", {"message": "Car heater charge mode error"})


def handle_car_heater_keep_at_temp(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Save comfort settings from the web UI; same contract as PATCH /comfort."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid keep-at-temp payload"})
        logger.warning("Bad keep_at_temp payload type: %s", type(data).__name__)
        return

    from flask_wtf.csrf import ValidationError, validate_csrf

    try:
        if not current_user.is_authenticated:
            raise ValueError("Authentication required")
        validate_csrf(data.get("csrf_token"))
    except (ValidationError, ValueError) as exc:
        handler.socketio.emit("error", {"message": str(exc)})
        return

    # Permission check
    if not current_user.is_admin:
        handler.flash("Permission denied for keep-at-temp settings.", "error")
        return

    from ..blueprints.api.car_heater.comfort import comfort_payload

    try:
        svc: KeepAtTempService | None = getattr(current_app, "keep_at_temp_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Keep-at-temp service not initialized"})
        return

    changes = {k: v for k, v in data.items() if k != "csrf_token"}
    try:
        svc.apply_changes(changes)
    except ValueError as e:
        # Includes ownership refusals; the saved state lets the page drop
        # its unsaved edit.
        handler.socketio.emit("error", {"message": str(e)})
        logger.warning("Comfort settings refused: %s", e)
    except Exception as e:
        logger.exception("keep_at_temp_settings error: %s", e)
        handler.socketio.emit("error", {"message": "Comfort settings were not saved"})
    handler.emit_to_views("keep_at_temp_settings", comfort_payload(svc))
