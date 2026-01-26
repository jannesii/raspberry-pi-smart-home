"""Car Heater Control module - Manual control and command queue.

Handles:
- Command queuing (turn on/off, restart)
- Command status tracking
- Charge mode state
- Historical data queries
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from ....extensions import csrf
from ....security import require_api_key
from ._blueprint import car_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if TYPE_CHECKING:
    from ....core import Controller
    from ....core.models import CarHeaterStatus
    from ....services.car_heater import CarHeaterService


# ==============================================================================
# Command Processing
# ==============================================================================


def process_commands(
    data: dict[str, Any],
    car: CarHeaterStatus | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    """
    Process car heater commands: update charge mode, handle results, fetch queued commands.

    Returns:
        Tuple of (commands, command_status, charge_mode_state)
    """
    commands: list[dict[str, Any]] = []
    command_status: dict[str, Any] | None = None
    charge_mode_state: dict[str, Any] | None = None
    logger.debug(
        "process_commands called data_keys=%s has_car=%s",
        list(data.keys()) if isinstance(data, dict) else None,
        car is not None,
    )

    try:
        from ....services.alert_webhook import record_alert

        service: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
        logger.debug("process_commands resolved car_heater_service=%s", service)
        if not service:
            return commands, command_status, charge_mode_state

        # Update charge mode from the latest Shelly status
        if car is not None:
            try:
                ts_iso = (
                    car.timestamp.isoformat()
                    if hasattr(car.timestamp, "isoformat")
                    else str(car.timestamp)
                )
                service.handle_status_update(
                    instant_power_w=car.instant_power_w,
                    is_heater_on=car.is_heater_on,
                    timestamp_iso=ts_iso,
                )
            except Exception as e:
                logger.exception("Failed to update car heater charge mode: %s", e)

        # Handle action results from ESP
        action_results = data.get("action_results", {})
        normalized_results: list[dict[str, Any]] = []
        if action_results:
            if isinstance(action_results, list):
                for item in action_results:
                    if isinstance(item, dict):
                        if "action" in item:
                            normalized_results.append(
                                {
                                    "action": item.get("action"),
                                    "success": item.get("success", item.get("ok", False)),
                                }
                            )
                        else:
                            for key, val in item.items():
                                normalized_results.append(
                                    {
                                        "action": key,
                                        "success": bool(val),
                                    }
                                )
                    elif isinstance(item, str):
                        normalized_results.append({"action": item, "success": True})
            elif isinstance(action_results, dict):
                if "action" in action_results:
                    normalized_results.append(
                        {
                            "action": action_results.get("action"),
                            "success": action_results.get(
                                "success", action_results.get("ok", False)
                            ),
                        }
                    )
                else:
                    for key, val in action_results.items():
                        normalized_results.append(
                            {
                                "action": key,
                                "success": bool(val),
                            }
                        )
            if normalized_results:
                service.mark_command_success(normalized_results)

        # Check queue length alert
        try:
            max_queue = int(os.getenv("CAR_HEATER_ALERT_QUEUE_LENGTH", "10"))
            queued_len = len(service.peek_queued_commands())
            if queued_len >= max_queue:
                record_alert(
                    key="car_heater_queue_backlog",
                    title="Car heater command backlog",
                    message=f"queued_commands={queued_len}",
                )
        except Exception:
            pass

        # Fetch and mark commands
        commands = service.get_queued_commands()
        service.mark_commands_sent(commands)

        command_status = asdict(service.get_command_status())
        try:
            charge_mode_state = asdict(service.get_charge_mode_state())
        except Exception:
            charge_mode_state = None

        logger.debug("cmd status: %s charge_mode: %s", command_status, charge_mode_state)

    except Exception as e:
        logger.exception("Failed to fetch queued car heater commands: %s", e)

    if commands:
        logger.info("Sending %s commands to car heater ESP", commands)

    return commands, command_status, charge_mode_state


# ==============================================================================
# Routes
# ==============================================================================


@car_bp.route("/commands", methods=["GET"])
@csrf.exempt
@require_api_key
def get_car_heater_commands():
    """Return currently queued car heater commands without consuming them."""
    logger.debug("get_car_heater_commands called")
    commands: list[dict[str, Any]] = []
    try:
        service: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
        if service:
            commands = service.peek_queued_commands()
            logger.debug("get_car_heater_commands returning %d commands", len(commands))
    except Exception as e:
        logger.exception("Failed to fetch queued car heater commands via GET: %s", e)

    return jsonify(commands), 200


@car_bp.route("/queue", methods=["POST"])
@csrf.exempt
@login_required
def queue_car_heater_command():
    """HTTP fallback for queuing car heater commands from the web UI."""
    data: dict[str, Any] = request.get_json() or {}
    action = (data.get("action") or "").strip()
    logger.debug("queue_car_heater_command called action=%s", action)
    if not action:
        return jsonify({"error": "Missing action"}), 400

    try:
        service: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
    except Exception:
        service = None

    if service is None:
        return jsonify({"error": "Car heater service not initialized"}), 503

    try:
        # Get username for logging
        username = current_user.get_id() if current_user.is_authenticated else "unknown"

        # Use centralized turn_on/turn_off methods
        if action == "turn_on":
            service.turn_on(source="http_api", reason=f"Manual control by {username}")
        elif action == "turn_off":
            service.turn_off(source="http_api", reason=f"Manual control by {username}")
        else:
            # Other commands (get_logs, esp_restart, etc.)
            service.queue_command({"action": action})

        logger.debug("Queued car heater command via HTTP: %s", action)
        return jsonify({"ok": True, "queued": {"action": action}}), 200
    except Exception as e:
        logger.exception("Failed to queue car heater command via HTTP: %s", e)
    return jsonify({"error": "Failed to queue command"}), 500


@car_bp.route("/history", methods=["GET"])
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

    logger.info("Serving car heater history for %s by %s", date_str, current_user.get_id())
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
    energies = [row.energy_total_wh for row in rows if row.energy_total_wh is not None]
    energy_today_wh = None
    if len(energies) >= 2:
        energy_today_wh = energies[-1] - energies[0]
    return jsonify({"rows": payload, "energy_today_wh": energy_today_wh}), 200
