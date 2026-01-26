"""
Ready-by scheduling socket event handlers.

All functions receive the SocketEventHandler instance as the first argument.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import current_app

if TYPE_CHECKING:
    from ..services.car_heater import ReadyByService
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def emit_ready_by_status_to_views(handler: SocketEventHandler) -> None:
    """Emit current Ready-by schedule status to all views."""
    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
    except Exception:
        svc = None
    if svc is None:
        return

    data = svc.ready_by_payload
    handler.emit_to_views("ready_by_status", data)


def handle_ready_by_schedule(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Handle Ready-by scheduling from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid ready-by payload"})
        logger.warning("Bad ready_by_schedule payload: %s", data)
        return

    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Ready-by service not initialized"})
        return

    action = (data.get("action") or "").strip()

    try:
        if action == "schedule":
            # Create a new schedule
            ready_by_raw = (data.get("ready_by_ts") or "").strip()
            if not ready_by_raw:
                handler.socketio.emit("error", {"message": "Missing ready_by_ts"})
                return
            try:
                ready_by_ts = datetime.fromisoformat(ready_by_raw.replace("Z", "+00:00"))
            except ValueError:
                handler.socketio.emit("error", {"message": "Invalid ready_by_ts format"})
                return

            target_raw = data.get("target_temp_c")
            if target_raw is None:
                handler.socketio.emit("error", {"message": "Missing target_temp_c"})
                return
            try:
                target_temp_c = float(target_raw)
            except (ValueError, TypeError):
                handler.socketio.emit("error", {"message": "Invalid target_temp_c"})
                return

            schedule = svc.schedule(ready_by_ts=ready_by_ts, target_temp_c=target_temp_c)
            logger.info(
                "Ready-by scheduled via socket: %s target=%.1f°C",
                schedule.ready_by_ts,
                target_temp_c,
            )
            emit_ready_by_status_to_views(handler)

        elif action == "cancel":
            reason = (data.get("reason") or "user").strip()
            turn_off = bool(data.get("turn_off", False))
            svc.cancel(reason=reason, turn_off=turn_off)
            logger.info("Ready-by canceled via socket (reason=%s)", reason)
            emit_ready_by_status_to_views(handler)

        elif action == "status":
            # Re-emit current schedule to requester(s)
            emit_ready_by_status_to_views(handler)

        else:
            handler.socketio.emit("error", {"message": f"Invalid ready-by action: {action}"})

    except Exception as e:
        logger.exception("ready_by_schedule error: %s", e)
        handler.socketio.emit("error", {"message": "Ready-by schedule error"})


def handle_ready_by_control(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Handle Ready-by control actions from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid ready-by control payload"})
        logger.warning("Bad ready_by_control payload: %s", data)
        return

    from ..services.car_heater import ReadyByConfig

    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
    except Exception:
        svc = None
    if svc is None:
        handler.socketio.emit("error", {"message": "Ready-by service not initialized"})
        return

    action = (data.get("action") or "").strip()
    if not action:
        handler.socketio.emit("error", {"message": "Missing action for ready-by control"})
        return

    try:
        if action == "update_config":
            config = data.get("config")
            svc.config = ReadyByConfig(**config)
            logger.info("Ready-by config set via socket %s", config)
            handler.emit_to_views("ready_by_config", {"enabled": svc.config.enabled})
    except Exception as e:
        logger.exception("ready_by_control error: %s", e)
        handler.socketio.emit("error", {"message": "Ready-by control error"})
