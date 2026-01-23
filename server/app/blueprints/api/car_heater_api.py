"""Car Heater API routes."""
from datetime import datetime, timezone
from typing import Any, Dict, List
import logging
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from ...core import Controller
from ...core.models import CarHeaterStatus
from ...extensions import csrf
from ...security import require_api_key

from .car_heater_utils import (
    parse_timestamp,
    parse_shelly_payload,
    build_car_heater_status,
    record_and_build_payload,
    build_fallback_payload,
    run_keep_at_temp_tick,
    process_commands,
    emit_status_to_views,
)

car_bp = Blueprint('car_bp', __name__, url_prefix='/car_heater')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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

    # Parse incoming data
    timestamp = parse_timestamp(data.get("timestamp"))
    shelly = parse_shelly_payload(data.get("shelly"))
    shelly_connected = bool(data.get("shelly_connected", True))
    ambient_temp = data.get("temperature")

    # Update global fallback status
    fallback_status.timestamp = timestamp
    fallback_status.ambient_temp = ambient_temp
    fallback_status.shelly_connected = shelly_connected

    # Build status and payload
    car: CarHeaterStatus | None = None
    status_payload: Dict[str, Any]

    if shelly and shelly_connected:
        car = build_car_heater_status(timestamp, shelly, ambient_temp)
        status_payload = record_and_build_payload(ctrl, car)
        run_keep_at_temp_tick(car)
    else:
        logger.debug("No shelly data provided in car heater status update")
        status_payload = build_fallback_payload(
            timestamp, ambient_temp, shelly_connected)

    # Process commands
    commands: List[Dict[str, Any]] = []
    command_status: Dict[str, Any] | None = None
    charge_mode_state: Dict[str, Any] | None = None

    if COMMANDS_ENABLED:
        commands, command_status, charge_mode_state = process_commands(
            data, car)
    else:
        logger.debug(
            "Car heater commands are disabled; skipping command queue handling.")

    # Notify browser clients
    emit_status_to_views(status_payload, command_status, charge_mode_state)

    elapsed_time = time.perf_counter() - start_time
    logger.debug(
        "Processed car heater status update in %.3f seconds", elapsed_time)

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


@car_bp.route('/queue', methods=['POST'])
@csrf.exempt
@login_required
def queue_car_heater_command():
    """
    HTTP fallback for queuing car heater commands from the web UI.
    """
    data: Dict[str, Any] = request.get_json() or {}
    action = (data.get("action") or "").strip()
    if not action:
        return jsonify({"error": "Missing action"}), 400

    try:
        from ...services.car_heater import CarHeaterService

        service: CarHeaterService | None = current_app.config.get(
            "CAR_HEATER_SERVICE")
    except Exception:
        service = None

    if service is None:
        return jsonify({"error": "Car heater service not initialized"}), 503

    try:
        cmd = {"action": action}
        service.queue_command(cmd)
        logger.debug("Queued car heater command via HTTP: %s", cmd)
        return jsonify({"ok": True, "queued": cmd}), 200
    except Exception as e:
        logger.exception("Failed to queue car heater command via HTTP: %s", e)
    return jsonify({"error": "Failed to queue command"}), 500


@car_bp.route('/history', methods=['GET'])
@login_required
def get_car_heater_history():
    """Return recorded car heater metrics for the requested day."""
    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

    tz = ZoneInfo("Europe/Helsinki")
    now_local = datetime.now(tz)
    default_date = now_local.date().isoformat()
    date_str = request.args.get("date", default_date).strip()

    try:
        # Validate the incoming date to avoid SQL injections and bogus queries
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400

    logger.info("Serving car heater history for %s by %s",
                date_str, current_user.get_id())
    rows = ctrl.get_car_heater_status_for_date(date_str)
    payload = [
        {
            "timestamp": row.timestamp,
            "instant_power_w": row.instant_power_w,
            "voltage_v": row.voltage_v,
            "current_a": row.current_a,
            "ambient_temp": row.ambient_temp,
            "device_temp_c": row.device_temp_c,
            "energy_total_wh": row.energy_total_wh,
        }
        for row in rows
    ]
    energies = [
        row.energy_total_wh for row in rows if row.energy_total_wh is not None]
    energy_today_wh = None
    if len(energies) >= 2:
        energy_today_wh = energies[-1] - energies[0]
    return jsonify({"rows": payload, "energy_today_wh": energy_today_wh}), 200
