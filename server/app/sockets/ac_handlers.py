"""
AC Thermostat socket event handlers.

All functions receive the SocketEventHandler instance as the first argument.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from flask import current_app
from flask_login import current_user

if TYPE_CHECKING:
    from ..services.ac import ACThermostat
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Parse booleans safely from socket payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int | float):
        return bool(value)
    value_s = str(value).strip().lower()
    if value_s in {"1", "true", "yes", "on"}:
        return True
    if value_s in {"0", "false", "no", "off", ""}:
        return False
    return default


def handle_ac_control(handler: SocketEventHandler, data: dict[str, Any]) -> None:
    """Handle AC thermostat control actions from the web UI."""
    if data is None or not isinstance(data, dict):
        handler.socketio.emit("error", {"message": "Invalid AC control payload"})
        logger.warning("Bad ac_control payload: %s", data)
        return
    elif not current_user.is_admin:
        handler.flash("You do not have permission to perform AC control actions.", "error")
        return

    action = (data.get("action") or "").strip()
    logger.info("Received ac_control: %s", action)

    ac_thermo: ACThermostat | None = getattr(current_app, "ac_thermostat", None)
    if ac_thermo is None:
        handler.flash("AC Thermostat service is not initialized.")
        logger.error("ac_control: thermostat missing")
        return

    try:
        if ac_thermo.winter:
            handler.flash("AC Thermostat is in winter mode; control actions are disabled.", "error")
            return

        if action == "power_on":
            ac_thermo.set_power(True)
            return
        if action == "power_off":
            ac_thermo.set_power(False)
            return
        if action == "thermostat_enable":
            ac_thermo.enable()
            return
        if action == "thermostat_disable":
            ac_thermo.disable()
            return
        if action == "set_mode":
            val = (data.get("value") or "").strip().lower()
            if val not in {"cold", "wet", "wind"}:
                handler.socketio.emit("error", {"message": f"Unsupported mode: {val}"})
                return
            ac_thermo.set_mode(val)
            return
        if action == "set_fan_speed":
            val = (data.get("value") or "").strip().lower()
            if val not in {"low", "high"}:
                handler.socketio.emit("error", {"message": f"Unsupported fan speed: {val}"})
                return
            ac_thermo.set_fan_speed(val)
            return
        if action == "set_setpoint":
            try:
                val = float(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid setpoint"})
                return
            ac_thermo.set_setpoint(val)
            return
        if action == "set_hysteresis":
            try:
                val = float(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid hysteresis"})
                return
            ac_thermo.set_hysteresis(val)
            return
        if action == "set_hysteresis_split":
            try:
                pos = float(data.get("pos"))
                neg = float(data.get("neg"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid hysteresis values"})
                return
            ac_thermo.set_hysteresis_split(pos, neg)
            return
        if action == "set_min_on_s":
            try:
                v = int(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid min_on_s"})
                return
            ac_thermo.set_min_on_s(v)
            return
        if action == "set_min_off_s":
            try:
                v = int(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid min_off_s"})
                return
            ac_thermo.set_min_off_s(v)
            return
        if action == "set_poll_interval_s":
            try:
                v = int(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid poll_interval_s"})
                return
            ac_thermo.set_poll_interval_s(v)
            return
        if action == "set_smooth_window":
            try:
                v = int(data.get("value"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid smooth_window"})
                return
            ac_thermo.set_smooth_window(v)
            return
        if action == "set_max_stale_s":
            raw = data.get("value")
            v = None
            if raw not in (None, ""):
                try:
                    v = int(raw)
                except Exception:
                    handler.socketio.emit("error", {"message": "Invalid max_stale_s"})
                    return
            ac_thermo.set_max_stale_s(v)
            return
        if action == "set_control_locations":
            locs = data.get("locations")
            if not isinstance(locs, list | tuple):
                handler.socketio.emit("error", {"message": "Invalid control locations"})
                return
            ac_thermo.set_control_locations(list(locs))
            return
        if action == "status":
            _emit_ac_status(handler, ac_thermo)
            return
        if action == "set_sleep_enabled":
            en = _parse_bool(data.get("value"))
            ac_thermo.set_sleep_enabled(en)
            return
        if action == "set_sleep_times":
            start = (data.get("start") or "").strip() or None
            stop = (data.get("stop") or "").strip() or None
            ac_thermo.set_sleep_times(start, stop)
            return
        if action == "set_sleep_schedule":
            sched = data.get("schedule")
            if not isinstance(sched, dict):
                handler.socketio.emit("error", {"message": "Invalid sleep schedule"})
                return
            ac_thermo.set_sleep_schedule(sched)
            return
        if action == "disable_sleep_for":
            try:
                minutes = int(data.get("minutes"))
            except Exception:
                handler.socketio.emit("error", {"message": "Invalid minutes for sleep override"})
                return
            ac_thermo.disable_sleep_for(minutes)
            return

        handler.socketio.emit("error", {"message": f"Invalid AC control action: {action}"})
        logger.warning("Bad ac_control action: %s", action)

    except RuntimeError as e:
        logger.warning("ac_control runtime error action=%s error=%s payload=%s", action, e, data)
        handler.flash(str(e), "error")
        handler.socketio.emit("error", {"message": str(e)})
    except Exception as e:
        logger.exception("ac_control error: %s", e)
        handler.socketio.emit("error", {"message": "AC control error"})


def _emit_ac_status(handler: SocketEventHandler, ac_thermo: ACThermostat) -> None:
    """Emit all AC status updates to views."""
    logger.debug("_emit_ac_status called ac_thermo=%s", ac_thermo)
    handler.emit_to_views("ac_status", {"is_on": bool(ac_thermo.is_on)})
    handler.emit_to_views(
        "thermostat_status",
        {
            "enabled": getattr(ac_thermo, "_enabled", True),
            "thermo_active": getattr(ac_thermo, "_enabled", True),
        },
    )

    # Emit current mode/fan
    try:
        st = ac_thermo.ac.get_status()
        mode = st.get("mode") if isinstance(st, dict) else None
        fan = st.get("fan_speed_enum") if isinstance(st, dict) else None
    except Exception:
        mode = None
        fan = None
    handler.emit_to_views("ac_state", {"mode": mode, "fan_speed": fan})

    # Emit sleep configuration
    payload = {
        "sleep_enabled": bool(getattr(ac_thermo.cfg, "sleep_active", True)),
        "sleep_start": getattr(ac_thermo.cfg, "sleep_start", None),
        "sleep_stop": getattr(ac_thermo.cfg, "sleep_stop", None),
        "sleep_time_active": bool(ac_thermo._is_sleep_time_window_now),
    }
    with contextlib.suppress(Exception):
        payload["sleep_schedule"] = getattr(ac_thermo.cfg, "sleep_weekly", None)
    handler.emit_to_views("sleep_status", payload)

    # Emit thermostat configuration
    handler.emit_to_views(
        "thermo_config",
        {
            "setpoint_c": float(getattr(ac_thermo.cfg, "target_temp", 0.0)),
            "pos_hysteresis": float(getattr(ac_thermo.cfg, "pos_hysteresis", 0.0)),
            "neg_hysteresis": float(getattr(ac_thermo.cfg, "neg_hysteresis", 0.0)),
            "control_locations": getattr(ac_thermo.cfg, "control_locations", None),
        },
    )
