"""Calibration Settings for the redesigned Settings view.

The HTTP routes and the ``kfactor_control``/``ready_by_control`` Socket.IO
events share ``app.services.car_heater.calibration_settings``, so validation,
bounded-session ownership and the persisted result are the same on both.
"""

from __future__ import annotations

import logging

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from ....services.car_heater.calibration_settings import (
    CALIBRATION_ACTIONS,
    NOT_SAVED,
    CalibrationRefused,
    apply_calibration_changes,
    calibration_data,
    calibration_payload,
    run_calibration_action,
)
from ._blueprint import car_bp

logger = logging.getLogger(__name__)


def _services():
    return (
        getattr(current_app, "kfactor_calibrator", None),
        getattr(current_app, "ready_by_service", None),
        getattr(current_app, "car_heater_service", None),
    )


def broadcast_calibration(payload) -> None:
    """Tell other open views about a saved change; optional when Socket.IO is absent."""
    handler = getattr(current_app, "sio_handler", None)
    if handler is None:
        return
    try:
        handler.emit_to_views("calibration_settings", payload)
    except Exception:
        logger.warning("Could not broadcast calibration settings", exc_info=True)


@car_bp.route("/calibration", methods=["GET", "PATCH"])
@login_required
def calibration_settings():
    kfactor, ready_by, heater = _services()
    if kfactor is None:
        return jsonify(error="Calibration is unavailable"), 503
    if request.method == "GET":
        return jsonify(calibration_payload(kfactor, ready_by, heater))
    if not getattr(current_user, "is_admin", False):
        logger.warning("Non-admin %s refused calibration change", current_user.get_id())
        return jsonify(error="Only administrators can change calibration"), 403
    try:
        apply_calibration_changes(kfactor, ready_by, heater, request.get_json(silent=True))
    except CalibrationRefused as exc:
        return jsonify(error=str(exc), **calibration_payload(kfactor, ready_by, heater)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Saving calibration settings failed")
        # Settings live in two records, so part of the change may have been kept.
        payload = calibration_payload(kfactor, ready_by, heater)
        broadcast_calibration(payload)
        return jsonify(error=NOT_SAVED, **payload), 500
    payload = calibration_payload(kfactor, ready_by, heater)
    broadcast_calibration(payload)
    return jsonify(payload)


@car_bp.route("/calibration/actions/<action>", methods=["POST"])
@login_required
def calibration_action(action):
    kfactor, ready_by, heater = _services()
    if action not in CALIBRATION_ACTIONS:
        return jsonify(error="Unknown calibration action"), 404
    if kfactor is None:
        return jsonify(error="Calibration is unavailable"), 503
    if not getattr(current_user, "is_admin", False):
        logger.warning("Non-admin %s refused calibration %s", current_user.get_id(), action)
        return jsonify(error="Only administrators can change calibration"), 403
    try:
        run_calibration_action(kfactor, action)
    except Exception:
        logger.exception("Calibration action %s failed", action)
        return jsonify(
            error="The calibration action did not complete",
            **calibration_payload(kfactor, ready_by, heater),
        ), 500
    payload = calibration_payload(kfactor, ready_by, heater)
    broadcast_calibration(payload)
    return jsonify(payload)


@car_bp.route("/calibration/data", methods=["GET"])
@login_required
def calibration_recorded_data():
    kfactor, _ready_by, _heater = _services()
    if kfactor is None:
        return jsonify(error="Calibration is unavailable"), 503
    return jsonify(calibration_data(kfactor))
