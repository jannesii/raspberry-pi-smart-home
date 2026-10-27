"""Root-Admin announcement API: status, preview, speaker tests, schedules, history.

Handlers validate and record requests only; synthesis and Cast work happen in
the announcement service loop. Browser mutations need CSRF (global
CSRFProtect); nothing here is exempt.
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_login import current_user, login_required

from ...services.announcements import AnnouncementInputError, AnnouncementStateError
from ...utils import require_root_admin_or_redirect

announcements_bp = Blueprint("api_announcements", __name__, url_prefix="/announcements")

logger = logging.getLogger(__name__)


def _error(status: int, error: str, message: str, **extra):
    return jsonify({"ok": False, "error": error, "message": message, **extra}), status


def root_admin_service(view):
    """Login + Root-Admin (fails closed) + an available service, in that order."""

    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        guard = require_root_admin_or_redirect("Root-Admin required", json=True)
        if guard:
            return guard
        service = getattr(current_app, "announcement_service", None)
        if service is None:
            return _error(
                503,
                "service_unavailable",
                "Announcement service is not running; check the server log.",
            )
        return view(service, *args, **kwargs)

    return wrapper


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _with_audio_url(preview: dict) -> dict:
    if preview.get("status") == "ready":
        preview = {**preview, "audio_url": f"/api/announcements/previews/{preview['id']}/audio"}
    return preview


@announcements_bp.route("/status", methods=["GET"])
@root_admin_service
def announcement_status(service):
    return jsonify({"ok": True, **service.status()})


@announcements_bp.route("/previews", methods=["POST"])
@root_admin_service
def create_preview(service):
    data = _body()
    try:
        preview = service.request_preview(
            text=data.get("text"),
            language=data.get("language"),
            chime_percent=data.get("chime_percent"),
            requested_by=current_user.get_id(),
        )
    except AnnouncementInputError as exc:
        return _error(400, "bad_request", str(exc), field=exc.field)
    return jsonify({"ok": True, "preview": _with_audio_url(preview)}), 202


@announcements_bp.route("/previews/<preview_id>", methods=["GET"])
@root_admin_service
def get_preview(service, preview_id):
    preview = service.get_preview(preview_id)
    if preview is None:
        return _error(404, "not_found", "Preview expired; prepare it again.")
    return jsonify({"ok": True, "preview": _with_audio_url(preview)})


@announcements_bp.route("/previews/<preview_id>/audio", methods=["GET"])
@root_admin_service
def get_preview_audio(service, preview_id):
    path = service.preview_audio(preview_id)
    if path is None:
        return _error(404, "not_found", "Preview audio is not available; prepare it again.")
    response = send_file(path, mimetype="audio/wav", conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@announcements_bp.route("/tests", methods=["GET"])
@root_admin_service
def list_tests(service):
    return jsonify({"ok": True, "tests": service.list_speaker_tests()})


@announcements_bp.route("/tests", methods=["POST"])
@root_admin_service
def create_test(service):
    data = _body()
    try:
        outcome, detail = service.request_speaker_test(
            text=data.get("text"),
            language=data.get("language"),
            target=data.get("target"),
            volume_percent=data.get("volume_percent"),
            chime_percent=data.get("chime_percent"),
            request_id=data.get("request_id"),
            requested_by=current_user.get_id(),
        )
    except AnnouncementInputError as exc:
        return _error(400, "bad_request", str(exc), field=exc.field)
    if outcome == "conflict":
        return _error(409, "conflict", "This request id was already used for a different test.")
    return jsonify({"ok": True, **detail}), 202 if outcome == "created" else 200


@announcements_bp.route("/tests/<test_id>", methods=["GET"])
@root_admin_service
def get_test(service, test_id):
    detail = service.speaker_test_detail(test_id)
    if detail is None:
        return _error(404, "not_found", "Speaker test not found.")
    return jsonify({"ok": True, **detail})


@announcements_bp.route("/tests/<test_id>/stop", methods=["POST"])
@root_admin_service
def stop_test(service, test_id):
    try:
        detail = service.request_stop(test_id)
    except AnnouncementStateError as exc:
        status = 404 if exc.code == "not_found" else 409
        return _error(status, exc.code, str(exc))
    return jsonify({"ok": True, **detail}), 202


# Schedules and reminder occurrences ------------------------------------------------
def _state_error(exc: AnnouncementStateError):
    status = 404 if exc.code == "not_found" else 409
    return _error(status, exc.code, str(exc))


_SCHEDULE_INPUT = (
    "kind",
    "text",
    "language",
    "target",
    "volume_percent",
    "chime_percent",
    "due_local",
    "fold",
    "recurrence",
    "expiry_minutes",
    "max_announcements",
    "repeat_interval_minutes",
    "urgent",
    "quiet_hours_override",
)


def _schedule_payload(data: dict) -> dict:
    return {key: data.get(key) for key in _SCHEDULE_INPUT}


@announcements_bp.route("/schedules", methods=["GET"])
@root_admin_service
def list_schedules(service):
    return jsonify({"ok": True, "schedules": service.list_schedules()})


@announcements_bp.route("/schedules", methods=["POST"])
@root_admin_service
def create_schedule(service):
    data = _body()
    try:
        outcome, detail = service.create_schedule(
            _schedule_payload(data),
            request_id=data.get("request_id"),
            requested_by=current_user.get_id(),
        )
    except AnnouncementInputError as exc:
        return _error(400, "bad_request", str(exc), field=exc.field)
    if outcome == "conflict":
        return _error(409, "conflict", "This request id was already used for a different schedule.")
    return jsonify({"ok": True, **detail}), 201 if outcome == "created" else 200


@announcements_bp.route("/schedules/<schedule_id>", methods=["GET"])
@root_admin_service
def get_schedule(service, schedule_id):
    detail = service.schedule_detail(schedule_id)
    if detail is None:
        return _error(404, "not_found", "Schedule not found.")
    return jsonify({"ok": True, **detail})


@announcements_bp.route("/schedules/<schedule_id>", methods=["PUT"])
@root_admin_service
def update_schedule(service, schedule_id):
    data = _body()
    revision = data.get("revision")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
        return _error(400, "bad_request", "revision must be a whole number.", field="revision")
    try:
        detail = service.update_schedule(schedule_id, _schedule_payload(data), revision=revision)
    except AnnouncementInputError as exc:
        return _error(400, "bad_request", str(exc), field=exc.field)
    except AnnouncementStateError as exc:
        return _state_error(exc)
    return jsonify({"ok": True, **detail})


@announcements_bp.route("/schedules/<schedule_id>/<action>", methods=["POST"])
@root_admin_service
def change_schedule_state(service, schedule_id, action):
    operations = {"pause": service.pause_schedule, "resume": service.resume_schedule}
    if action not in operations:
        return _error(404, "not_found", "Unknown schedule action.")
    try:
        detail = operations[action](schedule_id)
    except AnnouncementStateError as exc:
        return _state_error(exc)
    return jsonify({"ok": True, **detail})


@announcements_bp.route("/schedules/<schedule_id>", methods=["DELETE"])
@root_admin_service
def delete_schedule(service, schedule_id):
    """Removes the schedule from future delivery; occurrence history is kept."""
    try:
        detail = service.delete_schedule(schedule_id)
    except AnnouncementStateError as exc:
        return _state_error(exc)
    return jsonify({"ok": True, **detail})


@announcements_bp.route("/occurrences", methods=["GET"])
@root_admin_service
def list_occurrences(service):
    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit or 50, 200))
    return jsonify({"ok": True, "occurrences": service.list_occurrences(limit)})


@announcements_bp.route("/occurrences/<occurrence_id>", methods=["GET"])
@root_admin_service
def get_occurrence(service, occurrence_id):
    detail = service.occurrence_detail(occurrence_id)
    if detail is None:
        return _error(404, "not_found", "Occurrence not found.")
    return jsonify({"ok": True, **detail})


_OCCURRENCE_ACTIONS = {"done": "done", "skip": "skipped"}


@announcements_bp.route("/occurrences/<occurrence_id>/<action>", methods=["POST"])
@root_admin_service
def act_on_occurrence(service, occurrence_id, action):
    """Done, Skip this occurrence, or Stop speaking (which acknowledges nothing)."""
    try:
        if action == "stop":
            return jsonify({"ok": True, **service.stop_occurrence(occurrence_id)}), 202
        if action not in _OCCURRENCE_ACTIONS:
            return _error(404, "not_found", "Unknown occurrence action.")
        detail = service.acknowledge_occurrence(
            occurrence_id, _OCCURRENCE_ACTIONS[action], by=current_user.get_id()
        )
    except AnnouncementStateError as exc:
        return _state_error(exc)
    return jsonify({"ok": True, **detail})
