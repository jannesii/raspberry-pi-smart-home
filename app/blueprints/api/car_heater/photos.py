"""Authenticated observation photos. Deliberately independent of heater controls.

Uploads are raw JPEG bodies with metadata in the query string, so the CSRF check
never parses a multipart body before the size limit applies. The client chooses a
UUID per capture; retrying an identical upload returns the saved photo.
"""

import hashlib
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from flask import Response, current_app, jsonify, request
from flask_login import login_required

from ....core.models import CarHeaterPhoto
from ._blueprint import car_bp
from .sessions import _text, _time

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTOS_PER_SESSION = 200
MAX_CLOCK_AHEAD = timedelta(minutes=5)
MAX_SEQUENCE = 10000


def _uuid(value):
    try:
        if str(UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise ValueError("Id must be a lowercase UUID")


def _sequence(value):
    if value in (None, ""):
        return None
    if not (value.isascii() and value.isdigit()) or int(value) > MAX_SEQUENCE:
        raise ValueError("sequence must be a whole number from 0 to 10000")
    return int(value)


@car_bp.put("/sessions/<session_id>/photos/<photo_id>")
@login_required
def save_photo(session_id, photo_id):
    try:
        photo_id = _uuid(photo_id)
        if request.mimetype != "image/jpeg":
            raise ValueError("Upload a JPEG image")
        args = request.args
        kind = args.get("capture_kind")
        if kind not in ("scheduled", "manual"):
            raise ValueError("capture_kind must be scheduled or manual")
        captured = _time(args.get("captured_at"), "captured_at", required=True)
        if datetime.fromisoformat(captured) > datetime.now(UTC) + MAX_CLOCK_AHEAD:
            raise ValueError("captured_at is in the future; check the phone clock")
        due = _time(args.get("due_at"), "due_at", required=kind == "scheduled")
        if kind == "manual" and due is not None:
            raise ValueError("Manual photos have no due time")
        camera = _text(args.get("camera_label"), "camera_label")
        if camera and len(camera) > 200:
            raise ValueError("camera_label must be at most 200 characters")
        photo = CarHeaterPhoto(
            id=photo_id,
            session_id=session_id,
            captured_at=captured,
            due_at=due,
            capture_kind=kind,
            sequence=_sequence(args.get("sequence")),
            observation_started_at=_time(
                args.get("observation_started_at"), "observation_started_at"
            ),
            camera_label=camera,
            received_at=datetime.now(UTC).isoformat(),
            content_type="image/jpeg",
            byte_size=0,
            sha256="",
        )
    except ValueError as exc:
        logger.debug("Rejected photo session=%s: %s", session_id, exc)
        return jsonify(error=str(exc)), 400
    if request.content_length is None:
        return jsonify(error="Content-Length is required"), 411
    if request.content_length > MAX_PHOTO_BYTES:
        return jsonify(error="Photo is larger than 8 MB"), 413
    request.max_content_length = MAX_PHOTO_BYTES
    content = request.get_data(cache=False)
    if len(content) < 4 or not content.startswith(b"\xff\xd8\xff"):
        return jsonify(error="Upload a JPEG image"), 400
    photo.byte_size = len(content)
    photo.sha256 = hashlib.sha256(content).hexdigest()
    result, saved = current_app.ctrl.save_car_heater_photo(
        photo, content, max_per_session=MAX_PHOTOS_PER_SESSION
    )
    if result == "missing_session":
        logger.debug("Rejected photo for unknown session=%s", session_id)
        return jsonify(error="Session not found"), 404
    if result == "conflict":
        return jsonify(error="This photo id already holds a different image"), 409
    if result == "limit":
        logger.warning("Rejected photo for session=%s: per-session limit reached", session_id)
        return jsonify(error=f"A session can hold at most {MAX_PHOTOS_PER_SESSION} photos"), 409
    return jsonify(asdict(saved)), 201 if result == "created" else 200


@car_bp.get("/sessions/<session_id>/photos/<photo_id>")
@login_required
def photo_content(session_id, photo_id):
    try:
        photo_id = _uuid(photo_id)
    except ValueError:
        return jsonify(error="Photo not found"), 404
    found = current_app.ctrl.get_car_heater_photo_content(session_id, photo_id)
    if found is None:
        return jsonify(error="Photo not found"), 404
    content_type, content = found
    response = Response(content, mimetype=content_type)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=86400, immutable"
    response.headers["Content-Disposition"] = f'inline; filename="{photo_id}.jpg"'
    return response


MISSED_REASONS = ("page_hidden", "camera_unavailable", "capture_delayed", "upload_failed")
MAX_MISSED = 1000


def _missed(items):
    if not isinstance(items, list) or len(items) > MAX_MISSED:
        raise ValueError(f"missed must be a list of at most {MAX_MISSED} scheduled photos")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each missed photo must be an object")
        sequence = item.get("sequence")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not 0 <= sequence <= MAX_SEQUENCE
        ):
            raise ValueError("Missed sequence must be a whole number from 0 to 10000")
        if item.get("reason") not in MISSED_REASONS:
            raise ValueError("Missed reason must be one of " + ", ".join(MISSED_REASONS))
        result.append(
            {
                "sequence": sequence,
                "due_at": _time(item.get("due_at"), "due_at", required=True),
                "reason": item["reason"],
            }
        )
    return result


@car_bp.put("/sessions/<session_id>/observations/<observation_id>")
@login_required
def save_observation(session_id, observation_id):
    """Record a capture window. Evidence only: never calls heater controls."""
    try:
        observation_id = _uuid(observation_id)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        started = _time(data.get("started_at"), "started_at", required=True)
        finished = _time(data.get("finished_at"), "finished_at")
        if finished is not None and finished < started:
            raise ValueError("finished_at must not be before started_at")
        missed = _missed(data.get("missed"))
    except ValueError as exc:
        logger.debug("Rejected observation session=%s: %s", session_id, exc)
        return jsonify(error=str(exc)), 400
    result, saved = current_app.ctrl.save_car_heater_observation(
        observation_id, session_id, started, finished, missed
    )
    if result == "missing_session":
        return jsonify(error="Session not found"), 404
    if result == "conflict":
        logger.warning("Rejected observation id=%s: different session or start", observation_id)
        return jsonify(error="This observation belongs to another session or start time"), 409
    return jsonify(saved), 201 if result == "created" else 200
