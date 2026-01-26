"""BMP sensor API routes."""

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from flask import Blueprint, current_app, jsonify, request

from ...extensions import csrf

if TYPE_CHECKING:
    from ...core import Controller

bmp_bp = Blueprint("bmp_bp", __name__, url_prefix="/bmp")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _serialize_entry(entry) -> dict[str, Any]:
    """Serialize a BMP data entry to JSON-safe dict."""
    # Adjust these attribute names if your ORM uses different ones
    ts = entry.timestamp
    if isinstance(ts, datetime | date):
        ts = ts.isoformat()
    return {
        "id": getattr(entry, "id", None),
        "timestamp": ts,
        "temperature": getattr(entry, "temperature", None),
        "pressure": getattr(entry, "pressure", None),
        "altitude": getattr(entry, "altitude", None),
    }


@bmp_bp.route("/latest", methods=["GET"])
def get_latest_bmp_data():
    """Get the latest BMP sensor data (optionally N latest with ?limit=1..100)."""
    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

    data = ctrl.get_last_bmp_sensor_data()
    if data:
        return jsonify(_serialize_entry(data)), 200
    return jsonify({"error": "No data available"}), 404


@bmp_bp.route("/date", methods=["GET"])
def get_bmp_data_by_date():
    """Get BMP sensor data for a specific date (?date=YYYY-MM-DD)."""
    logger.debug("Received request for BMP data by date")
    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

    date_str = request.args.get("date")
    if not date_str:
        logger.debug("Missing date parameter in request")
        return jsonify({"error": "date query parameter is required (YYYY-MM-DD)"}), 400

    try:
        # Accept YYYY-MM-DD; expand to midnight..23:59:59 if your controller expects a range
        _ = datetime.fromisoformat(date_str)  # raises on bad format
    except ValueError:
        logger.debug("Invalid date format: %s", date_str)
        return jsonify({"error": "date must be in ISO format YYYY-MM-DD"}), 400

    data = ctrl.get_bmp_sensor_data_for_date(date_str)
    if not data:
        logger.debug("No BMP data found for date: %s", date_str)
        return jsonify([]), 200

    logger.debug("Returning %d BMP data entries for date: %s", len(data), date_str)
    return jsonify([_serialize_entry(e) for e in data]), 200


csrf.exempt(bmp_bp)
