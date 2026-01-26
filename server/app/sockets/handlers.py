"""
Socket.IO event handlers - main handler class.

Feature-specific handlers are extracted to separate modules:
- ac_handlers.py: AC thermostat control
- car_heater_handlers.py: Car heater control, charge mode, keep-at-temp
- ready_by_handlers.py: Ready-by scheduling
- kfactor_handlers.py: KFactor calibration
- log_stream_handlers.py: Log streaming
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import current_app, request
from flask_login import current_user

if TYPE_CHECKING:
    from flask_socketio import SocketIO

    from ..core import Controller
    from ..services.ac import ACThermostat
    from ..services.car_heater import KFactorCalibrator


# Import feature handlers
from .ac_handlers import handle_ac_control
from .car_heater_handlers import (
    emit_car_heater_status_to_views,
    handle_car_heater_charge_mode,
    handle_car_heater_control,
    handle_car_heater_keep_at_temp,
)
from .kfactor_handlers import (
    emit_kfactor_status,
    handle_kfactor_control,
)
from .log_stream_handlers import (
    handle_log_stream,
    maybe_stop_log_stream,
)
from .ready_by_handlers import (
    emit_ready_by_status_to_views,
    handle_ready_by_control,
    handle_ready_by_schedule,
)

if TYPE_CHECKING:
    from ..services.car_heater import KFactorCalibrator


class SocketEventHandler:
    """
    Singleton Socket.IO event handler.

    Manages connection tracking by role (view, client, esp32) and
    routes events to feature-specific handlers.
    """

    _instance: SocketEventHandler | None = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, socketio: SocketIO, ctrl: Controller):
        if self._initialized:
            return
        self.socketio = socketio
        self.ctrl = ctrl
        self.logger = logging.getLogger(__name__)

        # Track currently connected sids by role

        self.view_sids: set[str] = set()
        self.client_sids: set[str] = set()
        self.esp32_sids: set[str] = set()

        # Log stream state
        self._log_stream_sids: set[str] = set()
        self._log_stream_process = None

        self._initialized = True
        self._register_events(socketio)

    def _register_events(self, socketio: SocketIO) -> None:
        """Register all socket event handlers."""
        # Core events
        socketio.on_event("connect", self.handle_connect)
        socketio.on_event("disconnect", self.handle_disconnect)

        # Simple events (kept inline)
        socketio.on_event("image", self.handle_image)
        socketio.on_event("esp32_temphum", self.handle_esp32_temphum)
        socketio.on_event("status", self.handle_status)
        socketio.on_event("printerAction", self.handle_printer_action)

        # Feature events (delegated to extracted handlers)
        socketio.on_event("ac_control", lambda data: handle_ac_control(self, data))
        socketio.on_event("car_heater_control", lambda data: handle_car_heater_control(self, data))
        socketio.on_event(
            "car_heater_charge_mode", lambda data: handle_car_heater_charge_mode(self, data)
        )
        socketio.on_event(
            "keep_at_temp_settings", lambda data: handle_car_heater_keep_at_temp(self, data)
        )
        socketio.on_event("ready_by_schedule", lambda data: handle_ready_by_schedule(self, data))
        socketio.on_event("ready_by_control", lambda data: handle_ready_by_control(self, data))
        socketio.on_event("kfactor_control", lambda data: handle_kfactor_control(self, data))
        socketio.on_event("log_stream", lambda data: handle_log_stream(self, data))

    # =========================================================================
    # Emit helpers
    # =========================================================================

    def emit(self, event: str, payload: Any = None) -> None:
        """Emit event to all connected clients."""
        self.socketio.emit(event, payload)

    def emit_to_views(self, event: str, payload: Any = None) -> None:
        """Emit event only to currently connected browser views (by sid)."""
        for sid in list(self.view_sids):
            try:
                self.socketio.emit(event, payload, to=sid)
            except Exception:
                self.view_sids.discard(sid)
                self.logger.debug("Removed stale view sid: %s", sid)

    def emit_to_clients(self, event: str, payload: Any = None) -> None:
        """Emit event only to timelapse/pi clients (not views or esp32)."""
        for sid in list(self.client_sids):
            try:
                self.socketio.emit(event, payload, to=sid)
            except Exception:
                self.client_sids.discard(sid)
                self.logger.debug("Removed stale client sid: %s", sid)

    def emit_to_esp32(self, event: str, payload: Any = None) -> None:
        """Emit event only to esp32_server connections."""
        for sid in list(self.esp32_sids):
            try:
                self.socketio.emit(event, payload, to=sid)
            except Exception:
                self.esp32_sids.discard(sid)
                self.logger.debug("Removed stale esp32 sid: %s", sid)

    def flash(self, message: str, category: str = "info") -> None:
        """Emit a flash message to browser views."""
        self.emit_to_views("flash", {"category": category, "message": message})
        self.logger.info("Flash message: %s (%s)", category, message)

    # =========================================================================
    # Delegated emit methods (call extracted handlers)
    # =========================================================================

    def emit_car_heater_status_to_views(
        self,
        status_payload: dict[str, Any],
        command_status: dict[str, Any] | None,
        charge_mode_state: dict[str, Any] | None,
    ) -> None:
        """Emit car heater status to connected browser views via Socket.IO."""
        emit_car_heater_status_to_views(self, status_payload, command_status, charge_mode_state)

    def emit_ready_by_status_to_views(self) -> None:
        """Emit current Ready-by schedule status to all views."""
        emit_ready_by_status_to_views(self)

    def emit_kfactor_status(self, svc: KFactorCalibrator) -> None:
        """Helper to emit kFactor status to all views."""
        emit_kfactor_status(self, svc)

    # =========================================================================
    # Connection handlers
    # =========================================================================

    def handle_connect(self, auth) -> bool | None:
        """Handle new socket connection."""
        if not current_user.is_authenticated:
            self.logger.warning("Socket connect refused: unauthenticated user")
            return False

        sid = request.sid
        role = self._extract_role(auth)

        if sid:
            if role == "view":
                self.view_sids.add(sid)
                self.logger.info("View connected: %s (tracked)", sid)
            elif role == "client":
                self.client_sids.add(sid)
                self.logger.info("Client connected: %s (tracked)", sid)
            elif role == "esp32":
                self.esp32_sids.add(sid)
                self.logger.info("ESP32 server connected: %s (tracked)", sid)
            else:
                self.logger.info("Client connected (unclassified): %s", sid)
        return None

    def _extract_role(self, auth) -> str | None:
        """Extract and normalize role from auth payload or User-Agent."""
        role = None
        try:
            if isinstance(auth, dict):
                role = auth.get("role") or auth.get("type") or auth.get("client")
        except Exception:
            pass

        # Heuristic: browsers include 'mozilla' in UA
        if role is None:
            ua = str(request.headers.get("User-Agent", "")).lower()
            self.logger.debug("Socket connect UA: %s", ua)
            if "mozilla" in ua:
                return "view"
            return None

        role_l = str(role).lower()
        if role_l == "view":
            return "view"
        elif role_l in {"client", "raspi", "pi", "printer", "timelapse"}:
            return "client"
        elif role_l == "esp32":
            return "esp32"
        return None

    def handle_disconnect(self, *args) -> None:
        """Handle socket disconnection."""
        sid = request.sid
        removed = False

        if sid in self.view_sids:
            self.view_sids.discard(sid)
            removed = True
            self.logger.info("View disconnected: %s (untracked)", sid)
        if sid in self.client_sids:
            self.client_sids.discard(sid)
            removed = True
            self.logger.info("Client disconnected: %s (untracked)", sid)
        if sid in self.esp32_sids:
            self.esp32_sids.discard(sid)
            removed = True
            self.logger.info("ESP32 server disconnected: %s (untracked)", sid)

        # Clean up log stream subscription
        if sid in self._log_stream_sids:
            self._log_stream_sids.discard(sid)
            self.logger.info("Log stream subscriber disconnected: %s", sid)
            maybe_stop_log_stream(self)

        if not removed:
            self.logger.info("Client disconnected: %s", sid)

    # =========================================================================
    # Simple inline handlers
    # =========================================================================

    def handle_image(self, *args) -> None:
        """Broadcast image event to views."""
        self.emit_to_views("image")
        self.logger.debug("Emitted 'image' event")

    def handle_esp32_temphum(self, data: dict[str, Any]) -> None:
        """Handle ESP32 temperature/humidity data."""
        location = data.get("location")
        temp = data.get("temperature_c")
        hum = data.get("humidity_pct")

        if location is None or temp is None or hum is None:
            self.socketio.emit("error", {"message": "Invalid location/temperature/humidity data"})
            self.logger.warning("Bad esp32 temphum payload: %s", data)
            return

        # Derive current AC state if available
        ac_on_val = self._get_ac_state()

        saved = self.ctrl.record_esp32_temphum(location, temp, hum, ac_on=ac_on_val)
        self.emit_to_views(
            "esp32_temphum",
            {
                "location": saved.location,
                "temperature": saved.temperature,
                "humidity": saved.humidity,
                "ac_on": saved.ac_on,
            },
        )
        self.logger.debug("Broadcasted esp32 temphum: %s", data)

    def _get_ac_state(self) -> bool | None:
        """Get current AC on/off state from thermostat or DB."""
        try:
            ac_thermo: ACThermostat | None = getattr(current_app, "ac_thermostat", None)
            if ac_thermo is not None:
                return bool(ac_thermo.is_on)
            # Fallback to persisted DB flag
            conf = self.ctrl.get_thermostat_conf()
            if conf and conf.current_phase in ("on", "off"):
                return conf.current_phase == "on"
        except Exception:
            pass
        return None

    def handle_status(self, data: dict[str, Any]) -> None:
        """Broadcast status data to views."""
        if data is None:
            self.socketio.emit("error", {"message": "Invalid status data"})
            self.logger.warning("Bad status payload: %s", data)
            return
        self.emit_to_views("status", data)
        self.logger.debug("Broadcasted status: %s", data)

    def handle_printer_action(self, data: dict[str, Any]) -> None:
        """Handle printer action requests (currently disabled)."""
        result = data.get("result", "")
        if not current_user.is_admin and result == "":
            self.flash("You do not have permission to perform printer actions.", "error")
            return

        # This handler is currently disabled
        self.flash("Printer actions are currently disabled.", "error")
        return

        # Original implementation (unreachable)
        self.logger.info("Received printer action data: %s", data)
        action = data.get("action", "")
        if result != "":
            self._handle_client_response(result, action)
            return
        if action not in [
            "pause",
            "resume",
            "stop",
            "home",
            "timelapse_start",
            "timelapse_stop",
            "run_gcode",
        ]:
            self.socketio.emit("error", {"message": f"Invalid printer action: {action}"})
            self.logger.warning("Bad printer action: %s", action)
            return

        if action == "run_gcode":
            gcode = data.get("gcode", "")
            if not gcode:
                self.flash("No G-code provided for run_gcode action", "error")
                self.logger.error("No G-code provided for run_gcode action")
                return
            try:
                self.ctrl.record_gcode_command(gcode=gcode)
            except ValueError as e:
                self.logger.exception("Error recording G-code: %s", e)
                return

        self.emit_to_clients("printerAction", data)
        self.logger.info("Handled printer action: %s", action)

    def _handle_client_response(self, result: Any, action: str) -> None:
        """Handle printer client response."""
        if result is None:
            self.socketio.emit("error", {"message": "Invalid printer result"})
            self.logger.warning("Bad printer result: %s", result)
            return
        elif result == "":
            return

        if result:
            self.flash(f"Printer action '{action}' completed successfully.", "success")
        else:
            self.flash(f"Printer action '{action}' failed.", "error")
