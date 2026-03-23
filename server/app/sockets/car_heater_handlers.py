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
    from ..services.car_heater import CarHeaterService
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
                wd = weather_svc.get_latest()
                if wd:
                    payload["weather"] = {
                        "outside_temp_c": wd.t2m.value if wd.t2m else None,
                        "wind_speed_mps": wd.ws_10min.value if wd.ws_10min else None,
                    }
        except Exception:
            pass  # Weather data is optional

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

    try:
        # Get username for logging
        username = "unknown"
        if current_user and current_user.is_authenticated:
            username = current_user.get_id() or "unknown"
        logger.debug("handle_car_heater_control action=%s user=%s", action, username)

        # Use centralized turn_on/turn_off methods
        if action == "turn_on":
            svc.turn_on(source="web_ui", reason=f"Manual control by {username}")
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
    """Handle car heater keep-at-temperature settings from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid keep-at-temp payload"})
        logger.warning("Bad keep_at_temp payload: %s", data)
        return

    # Permission check
    if not current_user.is_admin:
        handler.flash("Permission denied for keep-at-temp settings.", "error")
        return

    from ..services.car_heater import KeepAtTempService, KeepAtTempSettings

    try:
        svc: KeepAtTempService | None = getattr(current_app, "keep_at_temp_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Keep-at-temp service not initialized"})
        return

    try:
        # Build partial update - only set fields that were provided
        current_settings = svc.get_settings() or KeepAtTempSettings()
        settings = KeepAtTempSettings(
            target_temperature_c=current_settings.target_temperature_c,
            hysteresis_c=current_settings.hysteresis_c,
            enabled=current_settings.enabled,
        )

        if "enabled" in data:
            settings.enabled = bool(data["enabled"])

        target_key = "target_temperature_c" if "target_temperature_c" in data else "target_temp_c"
        if target_key in data:
            temp = float(data[target_key])
            if not -50 <= temp <= 50:
                raise ValueError("Target temp out of range (-50 to 50°C)")
            settings.target_temperature_c = temp

        if "hysteresis_c" in data:
            hyst = float(data["hysteresis_c"])
            if not 0.1 <= hyst <= 10:
                raise ValueError("Hysteresis out of range (0.1 to 10°C)")
            settings.hysteresis_c = hyst

        svc.update_settings(settings)
        settings_new = svc.get_settings()

        handler.emit_to_views(
            "keep_at_temp_settings",
            {
                "enabled": settings_new.enabled,
                "target_temperature_c": settings_new.target_temperature_c,
                "hysteresis_c": settings_new.hysteresis_c,
            },
        )
    except ValueError as e:
        handler.socketio.emit("error", {"message": str(e)})
        logger.warning("Invalid keep-at-temp value: %s", e)
        if svc:
            current_settings = svc.get_settings()
            handler.emit_to_views(
                "keep_at_temp_settings",
                {
                    "enabled": current_settings.enabled,
                    "target_temperature_c": current_settings.target_temperature_c,
                    "hysteresis_c": current_settings.hysteresis_c,
                },
            )
    except Exception as e:
        logger.exception("keep_at_temp_settings error: %s", e)
        handler.socketio.emit("error", {"message": "Keep-at-temp settings error"})
