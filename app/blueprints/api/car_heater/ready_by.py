"""Ready-by (cabin target) schedule endpoints: get, create/replace, cancel.

Mutations are authenticated and browser-CSRF protected, like the matching
Socket.IO event.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from ._blueprint import car_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if TYPE_CHECKING:
    from ....services.car_heater import ReadyByService


def _emit_to_views() -> None:
    """Show HTTP changes in every open view, as the Socket.IO event does."""
    sio = getattr(current_app, "sio_handler", None)
    if sio is None:
        return
    try:
        sio.emit_ready_by_status_to_views()
    except Exception:
        logger.debug("Ready-by status broadcast failed", exc_info=True)


# ==============================================================================
# Routes
# ==============================================================================


@car_bp.route("/ready_by/schedule", methods=["GET"])
@login_required
def get_ready_by_schedule():
    """Return the current Ready-by schedule (if any)."""
    logger.debug("get_ready_by_schedule called")
    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
        logger.debug("get_ready_by_schedule resolved ready_by_service=%s", svc)
        if svc is None:
            return jsonify({"error": "ReadyByService not initialized"}), 503

        ready_by_data = svc.ready_by_payload
        return jsonify(ready_by_data), 200
    except Exception as e:
        logger.exception("Failed to get Ready-by schedule: %s", e)
    return jsonify({"error": "Failed to get schedule"}), 500


@car_bp.route("/ready_by/schedule", methods=["POST"])
@login_required
def create_ready_by_schedule():
    """Create a new Ready-by schedule."""
    logger.debug("create_ready_by_schedule called")
    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
        logger.debug("create_ready_by_schedule resolved ready_by_service=%s", svc)
        if svc is None:
            return jsonify({"error": "ReadyByService not initialized"}), 503

        data: dict[str, Any] = request.get_json() or {}

        # Parse ready_by_ts (ISO format or datetime-local format)
        ready_by_raw = (data.get("ready_by_ts") or "").strip()
        if not ready_by_raw:
            return jsonify({"error": "Missing ready_by_ts"}), 400
        try:
            # Support both ISO format and datetime-local (YYYY-MM-DDTHH:MM)
            ready_by_ts = datetime.fromisoformat(ready_by_raw.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "Invalid ready_by_ts format"}), 400

        # Parse target_temp_c
        target_raw = data.get("target_temp_c")
        if target_raw is None:
            return jsonify({"error": "Missing target_temp_c"}), 400
        try:
            target_temp_c = float(target_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid target_temp_c"}), 400

        schedule = svc.schedule(
            ready_by_ts=ready_by_ts,
            target_temp_c=target_temp_c,
        )

        logger.info(
            "Ready-by schedule created by %s: %s target=%.1f°C",
            current_user.get_id(),
            schedule.ready_by_ts,
            target_temp_c,
        )

        _emit_to_views()
        return jsonify({"ok": True, "schedule": asdict(schedule)}), 200
    except ValueError as exc:
        logger.info("Ready-by schedule refused for %s: %s", current_user.get_id(), exc)
        return jsonify(error=str(exc)), 409
    except Exception as e:
        logger.exception("Failed to create Ready-by schedule: %s", e)
    return jsonify({"error": "Failed to create schedule"}), 500


@car_bp.route("/ready_by/schedule", methods=["DELETE"])
@login_required
def cancel_ready_by_schedule():
    """Cancel the current Ready-by schedule."""
    logger.debug("cancel_ready_by_schedule called")
    try:
        svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
        logger.debug("cancel_ready_by_schedule resolved ready_by_service=%s", svc)
        if svc is None:
            return jsonify({"error": "ReadyByService not initialized"}), 503

        data: dict[str, Any] = request.get_json() or {}
        reason = (data.get("reason") or "user").strip()
        turn_off = bool(data.get("turn_off", False))

        schedule = svc.cancel(reason=reason, turn_off=turn_off)
        _emit_to_views()

        if schedule is None:
            return jsonify({"ok": True, "schedule": None, "message": "No active schedule"}), 200

        logger.info(
            "Ready-by schedule canceled by %s (reason=%s, turn_off=%s)",
            current_user.get_id(),
            reason,
            turn_off,
        )

        return jsonify({"ok": True, "schedule": asdict(schedule)}), 200
    except Exception as e:
        logger.exception("Failed to cancel Ready-by schedule: %s", e)
    return jsonify({"error": "Failed to cancel schedule"}), 500
