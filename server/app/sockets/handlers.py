import logging
from flask import request, current_app
from flask_socketio import SocketIO
from typing import Dict, Set, Any, TYPE_CHECKING
from flask_login import current_user

from ..core import Controller
from ..services.ac import ACThermostat
from ..services.car_heater import CarHeaterService

if TYPE_CHECKING:
    from ..services.car_heater import KFactorCalibrator


class SocketEventHandler:
    _instance: "SocketEventHandler | None" = None

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
        self.view_sids: Set[str] = set()
        self.client_sids: Set[str] = set()
        self.esp32_sids: Set[str] = set()
        self._initialized = True
        socketio.on_event('connect',    self.handle_connect)
        socketio.on_event('disconnect', self.handle_disconnect)
        socketio.on_event('image',      self.handle_image)
        socketio.on_event('esp32_temphum', self.handle_esp32_temphum)
        socketio.on_event('status',     self.handle_status)
        socketio.on_event('printerAction', self.handle_printer_action)
        socketio.on_event('ac_control',   self.handle_ac_control)
        socketio.on_event('car_heater_control',
                          self.handle_car_heater_control)
        socketio.on_event('car_heater_charge_mode',
                          self.handle_car_heater_charge_mode)
        socketio.on_event('keep_at_temp_settings',
                          self.handle_car_heater_keep_at_temp)
        socketio.on_event('ready_by_schedule',
                          self.handle_ready_by_schedule)
        socketio.on_event('ready_by_control',
                          self.handle_ready_by_control)
        socketio.on_event('kfactor_control',
                          self.handle_kfactor_control)
        socketio.on_event('log_stream',
                          self.handle_log_stream)

        # Track active log stream subscriptions
        self._log_stream_sids: Set[str] = set()
        self._log_stream_process = None

    def emit(self, event: str, payload: Any = None) -> None:
        """Emit event to all connected clients."""
        self.socketio.emit(event, payload)

    def handle_connect(self, auth):
        # Use Flask-Login session cookie for auth instead of API key
        if not current_user.is_authenticated:
            self.logger.warning("Socket connect refused: unauthenticated user")
            return False
        sid = request.sid  # type: ignore
        # Classify this connection
        role = None
        try:
            if isinstance(auth, dict):
                role = (auth.get('role') or auth.get(
                    'type') or auth.get('client'))
        except Exception:
            pass
        # Heuristic: consider browsers as view when UA looks like one
        is_view = False
        if role is None:
            ua = str(request.headers.get('User-Agent', '')).lower()
            # Very rough heuristic: browsers include 'mozilla' in UA
            self.logger.setLevel(logging.DEBUG)
            self.logger.debug("Socket connect UA: %s", ua)
            if 'mozilla' in ua:
                is_view = True
        # Normalize role
        role_l = str(role).lower() if role is not None else None
        if sid:
            if role_l == 'view' or is_view:
                self.view_sids.add(sid)
                self.logger.info("View connected: %s (tracked)", sid)
            elif role_l in {'client', 'raspi', 'pi', 'printer', 'timelapse'}:
                self.client_sids.add(sid)
                self.logger.info("Client connected: %s (tracked)", sid)
            elif role_l == 'esp32':
                self.esp32_sids.add(sid)
                self.logger.info("ESP32 server connected: %s (tracked)", sid)
            else:
                self.logger.info("Client connected (unclassified): %s", sid)
        else:
            self.logger.info("Client connected: %s", sid)

    def handle_disconnect(self, *args):
        """Allow any positional args to avoid signature mismatch."""
        sid = request.sid  # type: ignore
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
            self._maybe_stop_log_stream()
        if not removed:
            self.logger.info("Client disconnected: %s", sid)

    def emit_to_views(self, event: str, payload: Any = None) -> None:
        """Emit event only to currently connected browser views (by sid)."""
        # Iterate over a copy to avoid mutation during iteration
        for sid in list(self.view_sids):
            try:
                self.socketio.emit(event, payload, to=sid)
            except Exception:
                # If emit fails (e.g., stale sid), drop it
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

    def flash(self, message, category):
        """Emit a flash message to the client."""
        self.emit_to_views('flash', {'category': category, 'message': message})
        self.logger.info("Flash message: %s (%s)", category, message)

    def handle_image(self, *args):
        self.emit_to_views('image')
        self.logger.debug("Emitted 'image' event")

    def handle_esp32_temphum(self, data):
        location, temp, hum = data.get('location'), data.get(
            'temperature_c'), data.get('humidity_pct')
        if location is None or temp is None or hum is None:
            self.socketio.emit(
                'error', {'message': 'Invalid location/temperature/humidity data'})
            self.logger.warning("Bad esp32 temphum payload: %s", data)
            return
        # Derive current AC state if available
        try:
            from flask import current_app
            ac_thermo: ACThermostat | None = getattr(
                current_app, 'ac_thermostat', None)  # type: ignore
            ac_on_val: bool | None = bool(
                ac_thermo.is_on) if ac_thermo is not None else None
            if ac_on_val is None:
                # Fallback to persisted DB flag if thermostat not in memory
                conf = self.ctrl.get_thermostat_conf()
                if conf and conf.current_phase in ('on', 'off'):
                    ac_on_val = (conf.current_phase == 'on')
        except Exception:
            ac_on_val = None

        saved = self.ctrl.record_esp32_temphum(
            location, temp, hum, ac_on=ac_on_val)
        self.emit_to_views('esp32_temphum', {
            'location': saved.location,
            'temperature': saved.temperature,
            'humidity':    saved.humidity,
            'ac_on':       saved.ac_on
        })

        self.logger.debug("Broadcasted esp32 temphum: %s", data)

    def handle_status(self, data):
        if data is None:
            self.socketio.emit('error', {'message': 'Invalid status data'})
            self.logger.warning("Bad status payload: %s", data)
            return
        # saved = self.ctrl.update_status(data)
        self.emit_to_views('status', data)
        self.logger.debug("Broadcasted status: %s", data)

    def handle_client_response(self, result, action):
        if result is None:
            self.socketio.emit('error', {'message': 'Invalid printer result'})
            self.logger.warning("Bad printer result: %s", result)
            return
        elif result == '':
            return

        if result:
            self.flash(
                f"Printer action '{action}' completed successfully.",
                'success'
            )
        else:
            self.flash(
                f"Printer action '{action}' failed.",
                'error'
            )

    def handle_printer_action(self, data):
        result = data.get('result', '')
        if not current_user.is_admin and result == '':
            self.flash(
                "You do not have permission to perform printer actions.",
                'error'
            )
            return
        # This handler is currently disabled
        self.flash(
            "Printer actions are currently disabled.",
            'error'
        )
        return
        self.logger.info("Received printer action data: %s", data)
        action = data.get('action', '')
        if result != '':
            self.handle_client_response(result, action)
            return
        if action not in ['pause', 'resume', 'stop', 'home',
                          'timelapse_start', 'timelapse_stop',
                          'run_gcode'
                          ]:
            self.socketio.emit(
                'error', {'message': f'Invalid printer action: {action}'})
            self.logger.warning("Bad printer action: %s", action)
            return

        if action == 'run_gcode':
            gcode = data.get('gcode', '')
            if not gcode:
                self.flash(
                    "No G-code provided for run_gcode action",
                    'error'
                )
                self.logger.error("No G-code provided for run_gcode action")
                return
            try:
                result = self.ctrl.record_gcode_command(gcode=gcode)
            except ValueError as e:
                self.logger.exception("Error recording G-code: %s", e)
                return

        # Send printer action only to registered clients (Pi/Raspi)
        self.emit_to_clients('printerAction', data)
        self.logger.info("Handled printer action: %s", action)

    def handle_ac_control(self, data):
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid AC control payload'})
            self.logger.warning("Bad ac_control payload: %s", data)
            return
        elif not current_user.is_admin:
            self.flash(
                "You do not have permission to perform AC control actions.",
                'error'
            )
            return
        action = (data.get('action') or '').strip()
        self.logger.info("Received ac_control: %s", action)
        ac_thermo: ACThermostat | None = getattr(
            current_app, 'ac_thermostat', None)  # type: ignore
        if ac_thermo is None:
            self.flash(
                "AC Thermostat service is not initialized.",)
            self.logger.exception("ac_control: thermostat missing")
            return
        try:
            if ac_thermo.winter:
                self.flash(
                    "AC Thermostat is in winter mode; control actions are disabled.", 'error')
                return
            if action == 'power_on':
                ac_thermo.set_power(True)
                return
            if action == 'power_off':
                ac_thermo.set_power(False)
                return
            if action == 'thermostat_enable':
                ac_thermo.enable()
                return
            if action == 'thermostat_disable':
                ac_thermo.disable()
                return
            if action == 'set_mode':
                val = (data.get('value') or '').strip().lower()
                # User reports 'hot' unsupported
                if val not in {'cold', 'wet', 'wind'}:
                    self.socketio.emit(
                        'error', {'message': f'Unsupported mode: {val}'})
                    return
                ac_thermo.set_mode(val)
                return
            if action == 'set_fan_speed':
                val = (data.get('value') or '').strip().lower()
                # User reports 'mid' and 'auto' unsupported
                if val not in {'low', 'high'}:
                    self.socketio.emit(
                        'error', {'message': f'Unsupported fan speed: {val}'})
                    return
                ac_thermo.set_fan_speed(val)
                return
            if action == 'set_setpoint':
                try:
                    val = float(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid setpoint'})
                    return
                ac_thermo.set_setpoint(val)
                return
            if action == 'set_hysteresis':
                try:
                    val = float(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid hysteresis'})
                    return
                # Backward-compatible single value: split evenly
                ac_thermo.set_hysteresis(val)
                return
            if action == 'set_hysteresis_split':
                try:
                    pos = float(data.get('pos'))
                    neg = float(data.get('neg'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid hysteresis values'})
                    return
                ac_thermo.set_hysteresis_split(pos, neg)
                return
            if action == 'set_min_on_s':
                try:
                    v = int(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid min_on_s'})
                    return
                ac_thermo.set_min_on_s(v)
                return
            if action == 'set_min_off_s':
                try:
                    v = int(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid min_off_s'})
                    return
                ac_thermo.set_min_off_s(v)
                return
            if action == 'set_poll_interval_s':
                try:
                    v = int(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid poll_interval_s'})
                    return
                ac_thermo.set_poll_interval_s(v)
                return
            if action == 'set_smooth_window':
                try:
                    v = int(data.get('value'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid smooth_window'})
                    return
                ac_thermo.set_smooth_window(v)
                return
            if action == 'set_max_stale_s':
                # Allow null to disable stale filtering
                raw = data.get('value')
                v = None
                if raw not in (None, ''):
                    try:
                        v = int(raw)
                    except Exception:
                        self.socketio.emit(
                            'error', {'message': 'Invalid max_stale_s'})
                        return
                ac_thermo.set_max_stale_s(v)
                return
            if action == 'set_control_locations':
                locs = data.get('locations')
                if not isinstance(locs, (list, tuple)):
                    self.socketio.emit(
                        'error', {'message': 'Invalid control locations'})
                    return
                ac_thermo.set_control_locations(list(locs))
                return
            if action == 'status':
                # Re-emit current statuses to requester(s)
                self.emit_to_views(
                    'ac_status', {'is_on': bool(ac_thermo.is_on)})
                self.emit_to_views('thermostat_status', {
                    'enabled': getattr(ac_thermo, '_enabled', True),
                    'thermo_active': getattr(ac_thermo, '_enabled', True),
                })
                # Also emit current mode/fan
                try:
                    st = ac_thermo.ac.get_status()
                    mode = st.get('mode') if isinstance(st, dict) else None
                    fan = st.get('fan_speed_enum') if isinstance(
                        st, dict) else None
                except Exception:
                    mode = None
                    fan = None
                self.emit_to_views(
                    'ac_state', {'mode': mode, 'fan_speed': fan})
                # Emit current sleep configuration as well
                payload = {
                    'sleep_enabled': bool(getattr(ac_thermo.cfg, 'sleep_active', True)),
                    'sleep_start': getattr(ac_thermo.cfg, 'sleep_start', None),
                    'sleep_stop': getattr(ac_thermo.cfg, 'sleep_stop', None),
                    'sleep_time_active': bool(ac_thermo._is_sleep_time_window_now),
                }
                try:
                    payload['sleep_schedule'] = getattr(
                        ac_thermo.cfg, 'sleep_weekly', None)
                except Exception:
                    pass
                self.emit_to_views('sleep_status', payload)
                # Emit thermostat configuration
                self.emit_to_views('thermo_config', {
                    'setpoint_c': float(getattr(ac_thermo.cfg, 'target_temp', 0.0)),
                    'pos_hysteresis': float(getattr(ac_thermo.cfg, 'pos_hysteresis', 0.0)),
                    'neg_hysteresis': float(getattr(ac_thermo.cfg, 'neg_hysteresis', 0.0)),
                    'control_locations': getattr(ac_thermo.cfg, 'control_locations', None),
                })
                return
            if action == 'set_sleep_enabled':
                en = bool(data.get('value'))
                ac_thermo.set_sleep_enabled(en)
                return
            if action == 'set_sleep_times':
                start = (data.get('start') or '').strip() or None
                stop = (data.get('stop') or '').strip() or None
                ac_thermo.set_sleep_times(start, stop)
                return
            if action == 'set_sleep_schedule':
                sched = data.get('schedule')
                if not isinstance(sched, dict):
                    self.socketio.emit(
                        'error', {'message': 'Invalid sleep schedule'})
                    return
                ac_thermo.set_sleep_schedule(sched)
                return
            if action == 'disable_sleep_for':
                try:
                    minutes = int(data.get('minutes'))
                except Exception:
                    self.socketio.emit(
                        'error', {'message': 'Invalid minutes for sleep override'})
                    return
                ac_thermo.disable_sleep_for(minutes)
                return
            self.socketio.emit(
                'error', {'message': f'Invalid AC control action: {action}'})
            self.logger.warning("Bad ac_control action: %s", action)
        except Exception as e:
            self.logger.exception("ac_control error: %s", e)
            self.socketio.emit('error', {'message': 'AC control error'})

    def emit_car_heater_status_to_views(
        self,
        status_payload: Dict[str, Any],
        command_status: Dict[str, Any] | None,
        charge_mode_state: Dict[str, Any] | None,
    ) -> None:
        """Emit car heater status to connected browser views via Socket.IO."""
        try:
            payload: Dict[str, Any] = {"status": status_payload}
            if command_status is not None:
                payload["command_status"] = command_status
            if charge_mode_state is not None:
                payload["charge_mode"] = charge_mode_state
            self.emit("car_heater_status", payload)
        except Exception as e:
            self.logger.exception(
                "Failed to emit car_heater_status over Socket.IO: %s", e)

    def handle_car_heater_control(self, data):
        """
        Handle car heater actions from the web UI.

        Actions are queued via CarHeaterService; the ESP will fetch them
        on the next /car_heater/status POST.
        """
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid car heater payload'})
            self.logger.warning("Bad car_heater_control payload: %s", data)
            return
        action = (data.get('action') or '').strip()
        if not action:
            self.socketio.emit(
                'error', {'message': 'Missing car heater action'})
            return
        try:
            svc = getattr(current_app, 'car_heater_service', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'Car heater service not initialized'})
            return
        try:
            # Get username for logging
            username = "unknown"
            if current_user and current_user.is_authenticated:
                username = current_user.get_id() or "unknown"

            # Use centralized turn_on/turn_off methods
            if action == "turn_on":
                svc.turn_on(source="web_ui",
                            reason=f"Manual control by {username}")
            elif action == "turn_off":
                svc.turn_off(source="web_ui",
                             reason=f"Manual control by {username}")
            else:
                # Other commands (get_logs, esp_restart, etc.)
                svc.queue_command({'action': action})

            self.emit_to_views('car_heater_action_result', {
                'ok': True,
                'action': action,
                'label': f'Queued: {action}',
            })
        except Exception as e:
            self.logger.exception("car_heater_control error: %s", e)
            self.socketio.emit(
                'error', {'message': 'Car heater control error'})

    def handle_car_heater_charge_mode(self, data):
        """
        Toggle car heater battery charge mode from the web UI.
        """
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid car heater charge payload'})
            self.logger.warning(
                "Bad car_heater_charge_mode payload: %s", data)
            return

        if 'enabled' not in data:
            self.socketio.emit(
                'error', {'message': 'Missing enabled flag for charge mode'})
            return

        try:
            svc: CarHeaterService = getattr(
                current_app, 'car_heater_service', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'Car heater service not initialized'})
            return

        try:
            state = svc.set_charge_mode_enabled(bool(data.get('enabled')))
            payload = {
                'enabled': state.enabled,
                'threshold_w': state.threshold_w,
                'power_cut': state.power_cut,
                'power_cut_at': state.power_cut_at,
                'last_instant_power_w': state.last_instant_power_w,
            }
            self.emit_to_views('car_heater_charge_mode', payload)
        except Exception as e:
            self.logger.exception("car_heater_charge_mode error: %s", e)
            self.socketio.emit(
                'error', {'message': 'Car heater charge mode error'})

    def handle_car_heater_keep_at_temp(self, data):
        """Handle car heater keep-at-temperature settings from the web UI."""
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid keep-at-temp payload'})
            self.logger.warning("Bad keep_at_temp payload: %s", data)
            return

        # Permission check (if needed)
        if not current_user.is_admin:
            self.flash("Permission denied for keep-at-temp settings.", 'error')
            return

        from ..services.car_heater import KeepAtTempSettings, KeepAtTempService

        try:
            svc: KeepAtTempService | None = getattr(
                current_app, 'keep_at_temp_service', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'Keep-at-temp service not initialized'})
            return

        try:
            # Build partial update - only set fields that were provided
            current_settings = svc.get_settings() or KeepAtTempSettings()
            settings = KeepAtTempSettings(
                target_temperature_c=current_settings.target_temperature_c,
                hysteresis_c=current_settings.hysteresis_c,
                enabled=current_settings.enabled,
            )

            if 'enabled' in data:
                settings.enabled = bool(data['enabled'])

            target_key = 'target_temperature_c' if 'target_temperature_c' in data else 'target_temp_c'
            if target_key in data:
                temp = float(data[target_key])
                if not -50 <= temp <= 50:
                    raise ValueError("Target temp out of range (-50 to 50°C)")
                settings.target_temperature_c = temp

            if 'hysteresis_c' in data:
                hyst = float(data['hysteresis_c'])
                if not 0.1 <= hyst <= 10:
                    raise ValueError("Hysteresis out of range (0.1 to 10°C)")
                settings.hysteresis_c = hyst

            svc.update_settings(settings)
            settings_new = svc.get_settings()

            self.emit_to_views('keep_at_temp_settings', {
                'enabled': settings_new.enabled,
                'target_temperature_c': settings_new.target_temperature_c,
                'hysteresis_c': settings_new.hysteresis_c,
            })
        except ValueError as e:
            self.socketio.emit('error', {'message': str(e)})
            self.logger.warning("Invalid keep-at-temp value: %s", e)
            if svc:
                current_settings = svc.get_settings()
                self.emit_to_views('keep_at_temp_settings', {
                    'enabled': current_settings.enabled,
                    'target_temperature_c': current_settings.target_temperature_c,
                    'hysteresis_c': current_settings.hysteresis_c,
                })
        except Exception as e:
            self.logger.exception("keep_at_temp_settings error: %s", e)
            self.socketio.emit(
                'error', {'message': 'Keep-at-temp settings error'})

    def emit_ready_by_status_to_views(self):
        """Emit current Ready-by schedule status to all views."""
        from dataclasses import asdict
        from ..services.car_heater import ReadyByService

        try:
            svc: ReadyByService | None = getattr(
                current_app, 'ready_by_service', None)
        except Exception:
            svc = None
        if svc is None:
            return

        data = svc.ready_by_payload
        self.emit_to_views('ready_by_status', data)

    def handle_ready_by_schedule(self, data):
        """Handle Ready-by scheduling from the web UI."""
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid ready-by payload'})
            self.logger.warning("Bad ready_by_schedule payload: %s", data)
            return

        from dataclasses import asdict
        from datetime import datetime
        from ..services.car_heater import ReadyByService

        try:
            svc: ReadyByService | None = getattr(
                current_app, 'ready_by_service', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'Ready-by service not initialized'})
            return

        action = (data.get('action') or '').strip()

        try:
            if action == 'schedule':
                # Create a new schedule
                ready_by_raw = (data.get('ready_by_ts') or '').strip()
                if not ready_by_raw:
                    self.socketio.emit(
                        'error', {'message': 'Missing ready_by_ts'})
                    return
                try:
                    ready_by_ts = datetime.fromisoformat(
                        ready_by_raw.replace('Z', '+00:00'))
                except ValueError:
                    self.socketio.emit(
                        'error', {'message': 'Invalid ready_by_ts format'})
                    return

                target_raw = data.get('target_temp_c')
                if target_raw is None:
                    self.socketio.emit(
                        'error', {'message': 'Missing target_temp_c'})
                    return
                try:
                    target_temp_c = float(target_raw)
                except (ValueError, TypeError):
                    self.socketio.emit(
                        'error', {'message': 'Invalid target_temp_c'})
                    return

                schedule = svc.schedule(
                    ready_by_ts=ready_by_ts, target_temp_c=target_temp_c)
                self.logger.info("Ready-by scheduled via socket: %s target=%.1f°C",
                                 schedule.ready_by_ts, target_temp_c)
                self.emit_ready_by_status_to_views()

            elif action == 'cancel':
                reason = (data.get('reason') or 'user').strip()
                turn_off = bool(data.get('turn_off', False))
                schedule = svc.cancel(reason=reason, turn_off=turn_off)
                self.logger.info(
                    "Ready-by canceled via socket (reason=%s)", reason)
                self.emit_ready_by_status_to_views()

            elif action == 'status':
                # Re-emit current schedule to requester(s)
                self.emit_ready_by_status_to_views()

            else:
                self.socketio.emit(
                    'error', {'message': f'Invalid ready-by action: {action}'})

        except Exception as e:
            self.logger.exception("ready_by_schedule error: %s", e)
            self.socketio.emit('error', {'message': 'Ready-by schedule error'})

    def handle_ready_by_control(self, data):
        """Handle Ready-by control actions from the web UI."""
        if data is None or not isinstance(data, dict):
            self.socketio.emit(
                'error', {'message': 'Invalid ready-by control payload'})
            self.logger.warning("Bad ready_by_control payload: %s", data)
            return

        from ..services.car_heater import ReadyByService, ReadyByConfig

        try:
            svc: ReadyByService | None = getattr(
                current_app, 'ready_by_service', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'Ready-by service not initialized'})
            return

        action = (data.get('action') or '').strip()
        if not action:
            self.socketio.emit(
                'error', {'message': 'Missing action for ready-by control'})
            return

        try:
            if action == 'update_config':
                config = data.get('config')
                svc.config = ReadyByConfig(**config)
                self.logger.info(
                    "Ready-by config set via socket %s", config)
                self.emit_to_views('ready_by_config', {
                                   'enabled': svc.config.enabled})
        except Exception as e:
            self.logger.exception("ready_by_control error: %s", e)
            self.socketio.emit(
                'error', {'message': 'Ready-by control error'})

    def handle_kfactor_control(self, data):
        """Handle kFactor calibration control from the web UI."""
        if data is None or not isinstance(data, dict):
            self.socketio.emit('error', {'message': 'Invalid kfactor payload'})
            self.logger.warning("Bad kfactor_control payload: %s", data)
            return

        from ..services.car_heater import KFactorCalibrator
        from dataclasses import asdict

        try:
            svc: KFactorCalibrator | None = getattr(
                current_app, 'kfactor_calibrator', None)
        except Exception:
            svc = None
        if svc is None:
            self.socketio.emit(
                'error', {'message': 'KFactor service not initialized'})
            return

        action = (data.get('action') or '').strip()

        try:
            if action == 'set_enabled':
                enabled = bool(data.get('enabled', True))
                svc.enabled = enabled
                self.logger.info(
                    "KFactor enabled set to %s via socket", enabled)
                self.emit_kfactor_status(svc)

            elif action == 'status':
                self.emit_kfactor_status(svc)

            elif action == 'get_config':
                # Return full config for populating the UI
                config = asdict(svc.config)
                self.emit_to_views('kfactor_config', {'config': config})

            elif action == 'update_config':
                # Update config fields from the UI
                updates = data.get('config', {})
                if not isinstance(updates, dict):
                    self.socketio.emit(
                        'error', {'message': 'Invalid config payload'})
                    return

                # Get current config and merge updates
                current_config = asdict(svc.config)
                for key, value in updates.items():
                    if key in current_config:
                        # Type coercion based on current type
                        current_type = type(current_config[key])
                        try:
                            if current_type == bool:
                                current_config[key] = bool(value)
                            elif current_type == int:
                                current_config[key] = int(value)
                            elif current_type == float:
                                current_config[key] = float(value)
                            elif current_type == str:
                                current_config[key] = str(value)
                            else:
                                current_config[key] = value
                        except (TypeError, ValueError) as e:
                            self.logger.warning(
                                "kfactor config: invalid value for %s: %s", key, e)
                            continue

                # Create new config and save
                from ..services.car_heater.kfactor_calibrator import KFactorConfig
                new_config = KFactorConfig(**current_config)
                svc._cfg = new_config
                svc._save_config_in_db(new_config)

                self.logger.info(
                    "KFactor config updated via socket: %s", list(updates.keys()))

                # Emit updated config back to all views
                self.emit_to_views('kfactor_config', {
                    'config': asdict(new_config),
                    'saved': True,
                })

            elif action == 'reset_defaults':
                # Reset to default config
                from ..services.car_heater.kfactor_calibrator import KFactorConfig
                default_config = KFactorConfig()
                svc._cfg = default_config
                svc._save_config_in_db(default_config)

                self.logger.info("KFactor config reset to defaults via socket")

                self.emit_to_views('kfactor_config', {
                    'config': asdict(default_config),
                    'saved': True,
                    'reset': True,
                })

            elif action == 'extended_data':
                # Return extended snapshot with live session, history, bucket coverage, stats
                extended = svc.get_extended_snapshot()
                self.socketio.emit('kfactor_extended_data', extended)

            else:
                self.socketio.emit(
                    'error', {'message': f'Invalid kfactor action: {action}'})

        except Exception as e:
            self.logger.exception("kfactor_control error: %s", e)
            self.socketio.emit('error', {'message': 'KFactor control error'})

    def emit_kfactor_status(self, svc: "KFactorCalibrator") -> None:
        """Helper to emit kFactor status to all views."""
        snapshot = svc.get_debug_snapshot()
        self.emit_to_views('kfactor_status', {
            'state': snapshot.get('state'),
            'autonomous_enabled': snapshot.get('autonomous_enabled', False),
            'is_autonomous_session': snapshot.get('is_autonomous_session', False),
            'autonomous_cooldown_until': snapshot.get('autonomous_cooldown_until'),
            'passive_cooldown_until': snapshot.get('passive_cooldown_until'),
            'active_params': snapshot.get('active_params'),
            'last_session': snapshot.get('last_session'),
            'session_sample_count': snapshot.get('session_sample_count', 0),
            'config': snapshot.get('config', {}),
        })

    def handle_log_stream(self, data: Dict[str, Any]) -> None:
        """Handle log stream subscription/unsubscription."""
        if not current_user.is_authenticated:
            return
        if not getattr(current_user, 'is_admin', False):
            self.socketio.emit(
                'error', {'message': 'Admin required for log stream'})
            return

        sid = request.sid
        action = str(data.get('action', '')).lower()

        try:
            if action == 'subscribe':
                lines = int(data.get('lines', 50))
                lines = min(max(lines, 10), 500)  # Clamp 10-500

                self._log_stream_sids.add(sid)
                self.logger.info(
                    "Log stream subscriber added: %s (lines=%d)", sid, lines)

                # Start the stream if not running
                self._start_log_stream(lines)

            elif action == 'unsubscribe':
                self._log_stream_sids.discard(sid)
                self.logger.info("Log stream subscriber removed: %s", sid)
                self._maybe_stop_log_stream()

            else:
                self.socketio.emit(
                    'error', {'message': f'Invalid log_stream action: {action}'})

        except Exception as e:
            self.logger.exception("log_stream error: %s", e)
            self.socketio.emit('error', {'message': 'Log stream error'})

    def _start_log_stream(self, initial_lines: int = 50) -> None:
        """Start the journalctl subprocess if not running."""
        import subprocess
        import threading

        if self._log_stream_process is not None:
            # Already running
            return

        def stream_reader():
            try:
                # Start journalctl with follow mode
                # -o short-iso: cleaner timestamp format
                # Then we strip the hostname/process prefix in post-processing
                self._log_stream_process = subprocess.Popen(
                    ['/usr/bin/journalctl', '-u', 'jannenkoti.service',
                        '-f', '-n', str(initial_lines), '--no-pager', '-o', 'short-iso'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                for line in iter(self._log_stream_process.stdout.readline, ''):
                    if self._log_stream_process is None:
                        break
                    if not self._log_stream_sids:
                        break

                    line = line.rstrip('\n')
                    if line:
                        # Strip "hostname process[pid]: " prefix
                        # Format: "2026-01-25T17:16:48+0200 raspberrypi gunicorn[123]: actual message"
                        # We want: "17:16:48 actual message"
                        import re
                        match = re.match(
                            r'^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\+\d{4}\s+\S+\s+\S+\[\d+\]:\s*(.*)$', line)
                        if match:
                            line = f"{match.group(1)} {match.group(2)}"

                        # Emit to all subscribers
                        for sub_sid in list(self._log_stream_sids):
                            try:
                                self.socketio.emit(
                                    'log_line', {'line': line}, to=sub_sid)
                            except Exception:
                                self._log_stream_sids.discard(sub_sid)

            except Exception as e:
                self.logger.error("Log stream reader error: %s", e)
            finally:
                self._cleanup_log_stream()

        thread = threading.Thread(target=stream_reader, daemon=True)
        thread.start()

    def _maybe_stop_log_stream(self) -> None:
        """Stop the log stream if no subscribers remain."""
        if not self._log_stream_sids:
            self._cleanup_log_stream()

    def _cleanup_log_stream(self) -> None:
        """Clean up the journalctl subprocess."""
        if self._log_stream_process is not None:
            try:
                self._log_stream_process.terminate()
                self._log_stream_process.wait(timeout=2)
            except Exception:
                try:
                    self._log_stream_process.kill()
                except Exception:
                    pass
            self._log_stream_process = None
            self.logger.info("Log stream process terminated")
