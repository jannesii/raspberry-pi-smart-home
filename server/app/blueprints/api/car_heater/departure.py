"""Authenticated, CSRF-protected departure preview and explicit plan mutations."""

from flask import current_app, jsonify, request
from flask_login import login_required

from ._blueprint import car_bp


@car_bp.post("/departure/preview")
@login_required
def preview_departure():
    service = getattr(current_app, "departure_service", None)
    if service is None:
        return jsonify(error="Departure planner unavailable"), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    try:
        return jsonify(
            service.preview(
                data.get("departure_at"),
                fold=data.get("fold"),
                grace_minutes=data.get("grace_minutes", 30),
            )
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@car_bp.route("/departure", methods=["GET", "POST", "DELETE"])
@login_required
def departure_plan():
    service = getattr(current_app, "departure_service", None)
    if service is None:
        return jsonify(error="Departure planner unavailable"), 503
    if request.method == "GET":
        return jsonify(service.get_plan())
    if request.method == "DELETE":
        return jsonify(service.cancel())
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object"), 400
    try:
        return jsonify(
            service.save(
                data.get("departure_at"),
                fold=data.get("fold"),
                grace_minutes=data.get("grace_minutes", 30),
                expected_preview=data.get("expected_preview"),
            )
        ), 201
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
