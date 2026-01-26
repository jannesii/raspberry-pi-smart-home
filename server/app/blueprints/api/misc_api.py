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
from flask import Blueprint, current_app, jsonify, request, send_from_directory
from flask_login import current_user, login_required

from ...extensions import csrf

if typing.TYPE_CHECKING:
    from ...core import Controller

misc_bp = Blueprint("api_misc", __name__, url_prefix="")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
