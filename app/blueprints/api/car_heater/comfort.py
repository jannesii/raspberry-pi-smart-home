"""Comfort heating (Keep at Temperature) and its Heating preferences.

The HTTP route and the ``keep_at_temp_settings`` Socket.IO event share
``KeepAtTempService.apply_changes`` and ``comfort_payload``, so validation,
bounded-session ownership and the persisted result are the same on both.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from ._blueprint import car_bp

if TYPE_CHECKING:
    from ....services.car_heater import KeepAtTempService

logger = logging.getLogger(__name__)


def comfort_payload(svc: KeepAtTempService) -> dict[str, Any]:
    """Saved comfort values plus whether a start is currently allowed."""
    settings = svc.get_settings()
    reason = svc.unavailable_reason()
    return {
        "enabled": bool(settings and settings.enabled),
        "target_temperature_c": settings.target_temperature_c if settings else None,
        "hysteresis_c": settings.hysteresis_c if settings else None,
        "start_available": reason is None,
        "unavailable_reason": reason,
    }


def broadcast_comfort(svc: KeepAtTempService) -> None:
    """Tell other open views about a saved change; optional when Socket.IO is absent."""
    handler = getattr(current_app, "sio_handler", None)
    if handler is None:
        return
    try:
        handler.emit_to_views("keep_at_temp_settings", comfort_payload(svc))
    except Exception:
        logger.warning("Could not broadcast comfort settings", exc_info=True)


@car_bp.route("/comfort", methods=["GET", "PATCH"])
@login_required
def comfort_settings():
    from ....services.car_heater.keep_at_temp_service import ComfortUnavailable

    svc: KeepAtTempService | None = getattr(current_app, "keep_at_temp_service", None)
    if svc is None:
        return jsonify(error="Comfort heating is unavailable"), 503
    if request.method == "GET":
        return jsonify(comfort_payload(svc))
    if not getattr(current_user, "is_admin", False):
        logger.warning("Non-admin %s refused comfort settings change", current_user.get_id())
        return jsonify(error="Only administrators can change comfort heating"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify(error="Expected a JSON object with the fields to save"), 400
    try:
        svc.apply_changes(data)
    except ComfortUnavailable as exc:
        return jsonify(error=str(exc), **comfort_payload(svc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Saving comfort settings failed")
        return jsonify(error="Comfort settings were not saved"), 500
    broadcast_comfort(svc)
    return jsonify(comfort_payload(svc))
