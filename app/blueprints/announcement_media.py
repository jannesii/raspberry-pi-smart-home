"""Cookie-free playback capability route for Cast receivers.

A speaker fetches ``/announcement-media/<token>.wav`` from the configured
media origin. The token is the only authority: it names one prepared asset
until it expires or its attempt ends, and grants nothing else. Unknown,
expired and malformed tokens are indistinguishable (404).
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, current_app, send_file

announcement_media_bp = Blueprint("announcement_media", __name__)

logger = logging.getLogger(__name__)


@announcement_media_bp.route("/announcement-media/<token>.wav", methods=["GET"])
def announcement_media(token: str):
    service = getattr(current_app, "announcement_service", None)
    path = service.open_media(token) if service is not None else None
    if path is None:
        logger.info("Announcement media request refused (unknown or expired capability)")
        abort(404)
    response = send_file(path, mimetype="audio/wav", conditional=True, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex"
    return response
