"""Miscellaneous API routes.

Endpoints:
- /gcode (GET)
- /previewJpg (GET)
- /temphum (GET)
"""

from __future__ import annotations

import logging
import tempfile
import typing
from datetime import datetime

import pytz
from flask import Blueprint, current_app, g, jsonify, request, send_from_directory
from flask_login import current_user, login_required

from ...extensions import csrf
from ...security import require_api_key

if typing.TYPE_CHECKING:
    from ...core import Controller
    from ...services.hue.controller import HueController

misc_bp = Blueprint("api_misc", __name__, url_prefix="")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@misc_bp.route("/hue/set_current_color", methods=["POST"])
@require_api_key
@csrf.exempt
def set_hue_color():
    """Apply the configured Hue time slot immediately."""
    api_key_meta = getattr(g, "api_key", {}) or {}
    logger.info(
        "API /hue/set_current_color called by key_id=%s",
        api_key_meta.get("key_id", "unknown"),
    )
    hue_ctrl: HueController | None = getattr(current_app, "hue_ctrl", None)  # type: ignore[attr-defined]
    if hue_ctrl is None:
        logger.warning("Hue apply requested but app.hue_ctrl is not configured")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "hue_unavailable",
                    "message": "Hue controller is not configured.",
                }
            ),
            503,
        )

    apply_ok = hue_ctrl.apply_current_slot()
    logger.debug("Hue apply_current_slot result: ok=%s", apply_ok)
    if apply_ok:
        return jsonify(
            {
                "ok": True,
                "message": "Hue color updated based on current time slot.",
            }
        )
    return (
        jsonify(
            {
                "ok": False,
                "error": "hue_apply_failed",
                "message": "Failed to update Hue color.",
            }
        ),
        502,
    )


@misc_bp.route("/gcode")
@login_required
def get_gcode_commands():
    """
    Get all G-code commands from the database.
    """
    ctrl: Controller = current_app.ctrl  # type: ignore
    commands = ctrl.get_all_gcode_commands()
    return jsonify({"gcode_list": commands})


@misc_bp.route("/previewJpg")
@login_required
def serve_tmp_file():
    """
    Serve any file under /tmp via HTTP.
    """
    # Log every request for debugging
    logger.debug("Serving tmp file: preview.jpg")

    # In production, you may want to sanitize `path`!
    temp_dir = tempfile.gettempdir()
    return send_from_directory(temp_dir, "preview.jpg")


@misc_bp.route("/temphum")
@login_required
def get_temphum():
    ctrl: Controller = current_app.ctrl  # type: ignore
    finland_tz = pytz.timezone("Europe/Helsinki")
    date_str = request.args.get("date", datetime.now(finland_tz).date().isoformat())
    logger.info("API /temphum for %s by %s", date_str, current_user.get_id())
    data = ctrl.get_temphum_for_date(date_str)
    return jsonify(
        [
            {
                "timestamp": d.timestamp,
                "temperature": d.temperature,
                "humidity": d.humidity,
                "ac_on": getattr(d, "ac_on", None),
            }
            for d in data
        ]
    )


@misc_bp.route("test")
@csrf.exempt
def test_endpoint():
    logger.info("Test endpoint accessed")
    return "Test endpoint is working!"
