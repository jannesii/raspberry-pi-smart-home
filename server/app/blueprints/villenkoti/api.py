"""Villenkoti-specific API surface."""

from __future__ import annotations

import logging
import sqlite3
from functools import wraps
from typing import Any

from flask import Blueprint, jsonify, request

from ...extensions import csrf
from .controller import VillenkotiController

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

villenkoti_bp = Blueprint("villenkoti", __name__, url_prefix="/villenkoti")
controller = VillenkotiController()


def _extract_api_key() -> str | None:
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        return header.split(" ", 1)[1].strip()
    return request.headers.get("X-API-Key") or request.args.get("api_key")


def require_api_key(func: Any) -> Any:
    """Decorator that enforces Villenkoti API keys."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        api_key = _extract_api_key()
        if not api_key or not controller.verify_api_key(api_key):
            logger.warning("Villenkoti API access denied: missing or invalid API key")
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return func(*args, **kwargs)

    return wrapper


@villenkoti_bp.route("/sensor_readings", methods=["POST"])
@require_api_key
@csrf.exempt
def post_sensor_reading() -> tuple[Any, int]:
    payload = request.get_json(silent=True) or {}
    location = payload.get("location")
    temperature = payload.get("temperature_c")
    humidity = payload.get("humidity_pct")

    logger.debug("Received Villenkoti sensor reading: %s", payload)

    if location is None or temperature is None or humidity is None:
        logger.debug("Invalid Villenkoti sensor reading payload: %s", payload)
        return jsonify(
            {
                "ok": False,
                "error": "invalid_payload",
                "message": "location, temperature and humidity are required",
            }
        ), 400

    try:
        temp_value = float(temperature)
        humidity_value = float(humidity)
    except (TypeError, ValueError):
        logger.debug("Non-numeric temperature or humidity in payload: %s", payload)
        return jsonify(
            {
                "ok": False,
                "error": "invalid_payload",
                "message": "temperature and humidity must be numeric",
            }
        ), 400

    reading_id = controller.record_sensor_reading(
        location=location,
        temperature=temp_value,
        humidity=humidity_value,
    )

    return jsonify({"ok": True, "reading_id": reading_id}), 201


@villenkoti_bp.route("/execute_sql", methods=["POST"])
@require_api_key
@csrf.exempt
def execute_sql() -> tuple[Any, int]:
    payload = request.get_json(silent=True) or {}
    statement = payload.get("statement")
    params = payload.get("params")

    if not statement or not isinstance(statement, str):
        return jsonify(
            {
                "ok": False,
                "error": "invalid_payload",
                "message": "SQL statement is required",
            }
        ), 400

    if params is not None and not isinstance(params, list | tuple | dict):
        return jsonify(
            {
                "ok": False,
                "error": "invalid_payload",
                "message": "params must be an array or object if provided",
            }
        ), 400

    try:
        cursor = controller.execute_sql(statement, params)
    except sqlite3.DatabaseError as exc:
        logger.exception("Villenkoti SQL execution failed")
        return jsonify(
            {
                "ok": False,
                "error": "sql_error",
                "message": str(exc),
            }
        ), 400

    result: dict[str, Any] = {"ok": True}
    normalized_statement = statement.strip().lower()
    if normalized_statement.startswith("select"):
        rows = cursor.fetchall()
        result["rows"] = [dict(row) for row in rows]
    else:
        result["rows_affected"] = cursor.rowcount
        result["last_row_id"] = cursor.lastrowid

    return jsonify(result), 200
