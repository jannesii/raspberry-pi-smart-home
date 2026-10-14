"""Authenticated bounded manual heating, with browser-mutation CSRF checks."""

from flask import current_app, jsonify, request
from flask_login import login_required

from ._blueprint import car_bp


@car_bp.route("/bounded", methods=["GET", "POST", "DELETE"])
@login_required
def bounded_session():
    service = getattr(current_app, "car_heater_service", None)
    if service is None:
        return jsonify(error="Car heater service unavailable"), 503
    try:
        if request.method == "POST":
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error="Expected a JSON object"), 400
            return jsonify(service.start_bounded_session(data.get("duration_minutes", 60))), 201
        if request.method == "DELETE":
            return jsonify(service.stop_bounded_session())
        return jsonify(service.get_bounded_session())
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
