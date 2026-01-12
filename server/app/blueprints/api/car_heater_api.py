"""Car Heater API routes."""
from datetime import datetime, date, timezone
from typing import Any, Dict, List
import logging
import json
import time
from dataclasses import dataclass, asdict
from zoneinfo import ZoneInfo
from flask import Blueprint, request, jsonify, current_app
from ...core import Controller
from ...core.models import CarHeaterStatus
from ...extensions import csrf, socketio
from ...security import require_api_key

car_bp = Blueprint('car_bp', __name__, url_prefix='/car_heater')

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Toggle to enable/disable sending commands to the ESP.
COMMANDS_ENABLED = True


@dataclass
class FallbackStatus:
    timestamp: datetime | None = None
    ambient_temp: float | None = None
    shelly_connected: bool | None = None


# Global fallback status for car_heater_web
fallback_status = FallbackStatus()


@car_bp.route('/status', methods=['POST'])
@csrf.exempt
@require_api_key
def update_car_heater_status():
    """Update the car heater status and return queued commands."""
    start_time = time.perf_counter()
    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

    data: Dict[str, Any] = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Raw Shelly JSON payload from the ESP
    shelly_raw = data.get("shelly")

    raw_ts = data.get("timestamp")  # '2025-11-21 19:44:10'

    # Interpret the string as UTC
    dt_utc = datetime.strptime(
        raw_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    # Convert to Helsinki time (or whatever you use)
    dt_local = dt_utc.astimezone(ZoneInfo("Europe/Helsinki"))

    timestamp = dt_local

    shelly = None
    if shelly_raw:
        try:
            shelly = json.loads(shelly_raw)
        except json.JSONDecodeError:
            logger.warning("Failed to decode shelly JSON: %r", shelly_raw)
    if shelly is None:
        shelly = {}

    aenergy = shelly.get("aenergy") or {}
    shelly_temp = shelly.get("temperature") or {}

    shelly_connected = bool(data.get("shelly_connected", True))

    status_payload: Dict[str, Any] | None = None

    ambient_temp = data.get("temperature")
    fallback_status.timestamp = timestamp
    fallback_status.ambient_temp = ambient_temp
    fallback_status.shelly_connected = shelly_connected

    if shelly and shelly_connected:
        car = CarHeaterStatus(
            id=None,
            # Core status
            timestamp=timestamp,
            is_heater_on=bool(shelly.get("output")),        # True / False
            instant_power_w=shelly.get("apower", 0.0),       # e.g. 35.1
            voltage_v=shelly.get("voltage"),          # e.g. 229.7
            current_a=shelly.get("current"),          # e.g. 0.187

            # Energy (Wh)
            energy_total_wh=aenergy.get("total"),     # lifetime energy
            energy_last_min_wh=(aenergy.get("by_minute") or [None])[0],
            energy_ts=aenergy.get("minute_ts"),         # UNIX ts

            # Device temperature

            device_temp_c=shelly_temp.get("tC"),             # 37.9
            device_temp_f=shelly_temp.get("tF"),             # 100.1
            ambient_temp=ambient_temp,    # in Celsius

            # Optional/meta
            # 'button', 'HTTP_in', ...
            source=shelly.get("source")
        )

        # Persist status in DB
        recorded_id = None
        try:
            recorded = ctrl.record_car_heater_status(car)
            recorded_id = getattr(recorded, "id", None)
        except Exception as e:
            logger.exception("Failed to record car heater status: %s", e)

        status_payload = {
            "id": recorded_id,
            "timestamp": car.timestamp.isoformat() if hasattr(car.timestamp, "isoformat") else car.timestamp,
            "is_heater_on": car.is_heater_on,
            "instant_power_w": car.instant_power_w,
            "voltage_v": car.voltage_v,
            "current_a": car.current_a,
            "energy_total_wh": car.energy_total_wh,
            "energy_last_min_wh": car.energy_last_min_wh,
            "energy_ts": car.energy_ts,
            "device_temp_c": car.device_temp_c,
            "device_temp_f": car.device_temp_f,
            "ambient_temp": car.ambient_temp,
            "source": car.source,
        }
    else:
        logger.warning("No shelly data provided in car heater status update")
        status_payload = {
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
            "ambient_temp": ambient_temp,
            "shelly_connected": shelly_connected,
        }

    # Fetch any queued commands from the shared CarHeaterService
    commands: List[Dict[str, Any]] = []
    command_status: Dict[str, Any] | None = None
    if COMMANDS_ENABLED:
        try:
            from ...services.car_heater import CarHeaterService
            service: CarHeaterService = current_app.config.get(
                "CAR_HEATER_SERVICE")
            if service:
                action_results = data.get("action_results", {})
                if action_results:
                    service.mark_command_success(action_results)

                commands = service.get_queued_commands()
                service.mark_commands_sent(commands)
                command_status = asdict(service.get_command_status())
                logger.debug("cmd status: %s", command_status)
        except Exception as e:
            logger.exception(
                "Failed to fetch queued car heater commands: %s", e)
    else:
        logger.debug(
            "Car heater commands are disabled; skipping command queue handling.")
    if commands:
        logger.info("Sending %s commands to car heater ESP", commands)

    # Notify any connected browser views of the latest status + command statuses.
    # Use the global Socket.IO instance so events work across Gunicorn workers.
    try:
        if status_payload is not None:
            payload: Dict[str, Any] = {"status": status_payload}
            if command_status is not None:
                payload["command_status"] = command_status
            socketio.emit("car_heater_status", payload)
    except Exception as e:
        logger.exception(
            "Failed to emit car_heater_status over Socket.IO: %s", e)

    elapsed_time = time.perf_counter() - start_time
    logger.debug(
        "Processed car heater status update in %.3f seconds", elapsed_time)
    # Return commands as a plain JSON list so ESP can act on them
    return jsonify(commands), 200


@car_bp.route('/commands', methods=['GET'])
@csrf.exempt
@require_api_key
def get_car_heater_commands():
    """Return currently queued car heater commands without consuming them."""
    commands: List[Dict[str, Any]] = []
    try:
        from ...services.car_heater import CarHeaterService
        service: CarHeaterService = current_app.config.get(
            "CAR_HEATER_SERVICE")
        if service:
            commands = service.peek_queued_commands()
    except Exception as e:
        logger.exception(
            "Failed to fetch queued car heater commands via GET: %s", e)

    return jsonify(commands), 200
