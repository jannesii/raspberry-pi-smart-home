"""Car Heater KFactor module - Calibration endpoints.

Handles:
- KFactor debug snapshot
- KFactor status for frontend
- Ready-by ETA prediction
"""

import logging
from flask import request, jsonify, current_app
from flask_login import login_required

from ....core import Controller
from ....extensions import csrf
from ....security import require_api_key
from .status import fallback_status
from . import car_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ==============================================================================
# Routes
# ==============================================================================

@car_bp.route('/kfactor/debug', methods=['GET'])
@csrf.exempt
@require_api_key
def get_kfactor_debug():
    """Return KFactor calibration debug snapshot."""
    try:
        from ....services.car_heater import KFactorCalibrator

        svc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if svc is None:
            return jsonify({"error": "KFactorCalibrator not initialized"}), 503
        return jsonify(svc.get_debug_snapshot()), 200
    except Exception as e:
        logger.exception("Failed to return kfactor debug snapshot: %s", e)
    return jsonify({"error": "Failed to get kfactor debug snapshot"}), 500


@car_bp.route('/kfactor/status', methods=['GET'])
@login_required
def get_kfactor_status():
    """Return kFactor calibration status for the frontend."""
    try:
        from ....services.car_heater import KFactorCalibrator

        svc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if svc is None:
            return jsonify({"error": "KFactorCalibrator not initialized"}), 503

        snapshot = svc.get_debug_snapshot()

        # Return a simplified view for the frontend
        return jsonify({
            "state": snapshot.get("state"),
            "enabled": snapshot.get("config", {}).get("enabled", True),
            "cooldown_until": snapshot.get("cooldown_until"),
            "active_params": snapshot.get("active_params"),
            "last_session": snapshot.get("last_session"),
            "session_sample_count": snapshot.get("session_sample_count", 0),
        }), 200
    except Exception as e:
        logger.exception("Failed to get kFactor status: %s", e)
    return jsonify({"error": "Failed to get kFactor status"}), 500


@car_bp.route('/ready_by', methods=['GET'])
@login_required
def get_ready_by_prediction():
    """Return a Ready-by ETA prediction (minutes) using the calibrated model."""
    try:
        from ....services.car_heater import KFactorCalibrator

        svc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if svc is None:
            return jsonify({"error": "KFactorCalibrator not initialized"}), 503

        # Parse target_temp_c (required)
        target_raw = (request.args.get("target_temp_c") or "").strip()
        if not target_raw:
            return jsonify({"error": "Missing target_temp_c"}), 400
        try:
            target_temp_c = float(target_raw)
        except ValueError:
            return jsonify({"error": "Invalid target_temp_c"}), 400

        # Parse optional cabin_temp_c
        cabin_raw = (request.args.get("cabin_temp_c") or "").strip()
        cabin_temp_c: float | None = None
        if cabin_raw:
            try:
                cabin_temp_c = float(cabin_raw)
            except ValueError:
                return jsonify({"error": "Invalid cabin_temp_c"}), 400

        # Parse optional outside_temp_c
        outside_raw = (request.args.get("outside_temp_c") or "").strip()
        outside_temp_c: float | None = None
        if outside_raw:
            try:
                outside_temp_c = float(outside_raw)
            except ValueError:
                return jsonify({"error": "Invalid outside_temp_c"}), 400

        # Parse optional power_w
        power_raw = (request.args.get("power_w") or "").strip()
        power_w: float | None = None
        if power_raw:
            try:
                power_w = float(power_raw)
            except ValueError:
                return jsonify({"error": "Invalid power_w"}), 400

        # Get last status for fallback values
        ctrl: Controller = getattr(current_app, "ctrl", None)
        last = ctrl.get_last_car_heater_status() if ctrl is not None else None

        # Fallback for cabin temperature
        if cabin_temp_c is None:
            if last is not None and last.ambient_temp is not None:
                cabin_temp_c = float(last.ambient_temp)
            elif fallback_status.ambient_temp is not None:
                cabin_temp_c = float(fallback_status.ambient_temp)

        if cabin_temp_c is None:
            return jsonify({"error": "No cabin temperature available"}), 503

        # Fallback for outside temperature
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

        # Fallback for power
        if power_w is None and last is not None:
            try:
                power_w = float(last.instant_power_w)
            except Exception:
                power_w = None

        # Calculate ETA
        eta_min = svc.predict_time_to_target_minutes(
            cabin_temp_c=cabin_temp_c,
            target_temp_c=target_temp_c,
            outside_temp_c=outside_temp_c,
            power_w=power_w,
        )

        k_loss, eta = svc.get_active_params(outside_temp_c=outside_temp_c)
        return jsonify({
            "time_to_target_min": eta_min,
            "reachable": eta_min is not None,
            "active_params": {"k_loss_W_per_K": k_loss, "eta": eta},
            "inputs": {
                "cabin_temp_c": cabin_temp_c,
                "target_temp_c": target_temp_c,
                "outside_temp_c": outside_temp_c,
                "power_w": power_w,
            },
        }), 200
    except Exception as e:
        logger.exception("Failed to compute Ready-by prediction: %s", e)
    return jsonify({"error": "Failed to compute prediction"}), 500
