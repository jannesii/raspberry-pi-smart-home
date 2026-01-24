"""Car Heater API routes."""
from datetime import datetime
import time
from typing import Any, Dict, List
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from ...core import Controller
from ...extensions import csrf
from ...security import require_api_key

from .car_heater_utils import handle_status_update_request

car_bp = Blueprint('car_bp', __name__, url_prefix='/car_heater')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Toggle to enable/disable sending commands to the ESP.
COMMANDS_ENABLED = True

# Test mode timeout: normal endpoint disabled while test requests arrive
TEST_MODE_TIMEOUT_SECONDS = 10.0
_last_test_request_time: float | None = None
_test_mode_was_active: bool = False


def _is_test_mode_active() -> bool:
    """Check if test mode is active (test request received within timeout)."""
    if _last_test_request_time is None:
        return False
    return (time.monotonic() - _last_test_request_time) < TEST_MODE_TIMEOUT_SECONDS


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
    global _test_mode_was_active

    if _is_test_mode_active():
        logger.debug("Normal endpoint blocked - test mode active")
        return jsonify([]), 200  # Return empty commands, don't process

    # Log when test mode expires
    if _test_mode_was_active:
        logger.info("Test mode expired - normal endpoint re-enabled")
        _test_mode_was_active = False

    return handle_status_update_request(
        data=request.get_json(),
        fallback_status=fallback_status,
        commands_enabled=COMMANDS_ENABLED,
    )


@car_bp.route('/status/test', methods=['POST'])
@csrf.exempt
@require_api_key
def update_car_heater_status_test():
    """Test endpoint for car heater status (requires API key)."""
    global _last_test_request_time, _test_mode_was_active

    # Log if test mode is being activated (not already active)
    if not _is_test_mode_active():
        logger.info(
            "Test mode activated - normal endpoint disabled for %.0f seconds",
            TEST_MODE_TIMEOUT_SECONDS,
        )
    _last_test_request_time = time.monotonic()
    _test_mode_was_active = True

    return handle_status_update_request(
        data=request.get_json(),
        fallback_status=fallback_status,
        commands_enabled=COMMANDS_ENABLED,
        is_test=True,
    )


@car_bp.route('/commands', methods=['GET'])
@csrf.exempt
@require_api_key
def get_car_heater_commands():
    """Return currently queued car heater commands without consuming them."""
    commands: List[Dict[str, Any]] = []
    try:
        from ...services.car_heater import CarHeaterService
        service: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
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

        service: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
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


@car_bp.route('/kfactor/debug', methods=['GET'])
@csrf.exempt
@require_api_key
def get_kfactor_debug():
    """Return KFactor calibration debug snapshot."""
    try:
        from ...services.car_heater import KFactorCalibrator

        svc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if svc is None:
            return jsonify({"error": "KFactorCalibrator not initialized"}), 503
        return jsonify(svc.get_debug_snapshot()), 200
    except Exception as e:
        logger.exception("Failed to return kfactor debug snapshot: %s", e)
    return jsonify({"error": "Failed to get kfactor debug snapshot"}), 500


@car_bp.route('/ready_by', methods=['GET'])
@login_required
def get_ready_by_prediction():
    """Return a Ready-by ETA prediction (minutes) using the calibrated model."""
    try:
        from ...services.car_heater import KFactorCalibrator

        svc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if svc is None:
            return jsonify({"error": "KFactorCalibrator not initialized"}), 503

        target_raw = (request.args.get("target_temp_c") or "").strip()
        if not target_raw:
            return jsonify({"error": "Missing target_temp_c"}), 400
        try:
            target_temp_c = float(target_raw)
        except ValueError:
            return jsonify({"error": "Invalid target_temp_c"}), 400

        cabin_raw = (request.args.get("cabin_temp_c") or "").strip()
        cabin_temp_c: float | None = None
        if cabin_raw:
            try:
                cabin_temp_c = float(cabin_raw)
            except ValueError:
                return jsonify({"error": "Invalid cabin_temp_c"}), 400

        outside_raw = (request.args.get("outside_temp_c") or "").strip()
        outside_temp_c: float | None = None
        if outside_raw:
            try:
                outside_temp_c = float(outside_raw)
            except ValueError:
                return jsonify({"error": "Invalid outside_temp_c"}), 400

        power_raw = (request.args.get("power_w") or "").strip()
        power_w: float | None = None
        if power_raw:
            try:
                power_w = float(power_raw)
            except ValueError:
                return jsonify({"error": "Invalid power_w"}), 400

        ctrl: Controller = getattr(current_app, "ctrl", None)
        last = ctrl.get_last_car_heater_status() if ctrl is not None else None

        if cabin_temp_c is None:
            if last is not None and last.ambient_temp is not None:
                cabin_temp_c = float(last.ambient_temp)
            elif fallback_status.ambient_temp is not None:
                cabin_temp_c = float(fallback_status.ambient_temp)

        if cabin_temp_c is None:
            return jsonify({"error": "No cabin temperature available"}), 503

        if outside_temp_c is None:
            try:
                wsvc = getattr(current_app, "weather_service", None)
                w = wsvc.get_latest() if wsvc is not None else None
                if w is not None and w.t2m is not None:
                    outside_temp_c = float(w.t2m.value)
            except Exception:
                outside_temp_c = None

        if outside_temp_c is None:
            return jsonify({"error": "No outside temperature available"}), 503

        if power_w is None and last is not None:
            try:
                power_w = float(last.instant_power_w)
            except Exception:
                power_w = None

        eta_min = svc.predict_time_to_target_minutes(
            cabin_temp_c=cabin_temp_c,
            target_temp_c=target_temp_c,
            outside_temp_c=outside_temp_c,
            power_w=power_w,
        )

        k_loss, eta = svc.get_active_params(outside_temp_c=outside_temp_c)
        return jsonify(
            {
                "time_to_target_min": eta_min,
                "reachable": eta_min is not None,
                "active_params": {"k_loss_W_per_K": k_loss, "eta": eta},
                "inputs": {
                    "cabin_temp_c": cabin_temp_c,
                    "target_temp_c": target_temp_c,
                    "outside_temp_c": outside_temp_c,
                    "power_w": power_w,
                },
            }
        ), 200
    except Exception as e:
        logger.exception("Failed to compute Ready-by prediction: %s", e)
    return jsonify({"error": "Failed to compute prediction"}), 500
