"""Authenticated driver evidence. Deliberately independent of heater controls."""

import math
import os
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, jsonify, request
from flask_login import login_required

from ....services.car_heater.session_review import build_session_review
from ._blueprint import car_bp

OUTCOMES = ("clear", "wipers_enough", "partial_scrape", "full_scrape")


def _time(value, name, *, required=False):
    if value is None or value == "":
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        raise ValueError(f"{name} must include a timezone offset")


def _text(value, name):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError(f"{name} must be text of at most 2000 characters")
    return value.strip() or None


def _data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


@car_bp.get("/sessions")
@login_required
def list_sessions():
    start = end = None
    try:
        if request.args.get("date"):
            day = date.fromisoformat(request.args["date"])
            local = datetime.combine(day, datetime.min.time(), ZoneInfo("Europe/Helsinki"))
            start = local.astimezone(UTC).isoformat()
            end = (local + timedelta(days=1)).astimezone(UTC).isoformat()
    except ValueError:
        return jsonify(error="Invalid date"), 400
    rows = current_app.ctrl.list_car_heater_sessions(start, end)
    photos = current_app.ctrl.summarize_car_heater_photos([s.id for s in rows])
    no_photos = {"saved": 0, "first_captured_at": None, "last_captured_at": None}
    return jsonify([{**asdict(s), "photos": photos.get(s.id, no_photos)} for s in rows])


@car_bp.post("/sessions")
@login_required
def create_session():
    try:
        data = _data()
        trigger = data.get("trigger", "observation")
        if trigger not in ("manual", "observation", "comfort", "ready_by"):
            raise ValueError("Invalid session trigger")
        observed = _time(data.get("observed_at"), "observed_at", required=True)
        departure = _time(data.get("requested_departure_at"), "requested_departure_at")
        duration = data.get("heating_duration_minutes")
        if duration is not None and (
            isinstance(duration, bool)
            or not isinstance(duration, int | float)
            or not math.isfinite(duration)
            or not 0 <= duration <= 10000
        ):
            raise ValueError("Heating duration must be a finite number of minutes from 0 to 10000")
        newest = current_app.ctrl.get_last_car_heater_sensor_frame()
        recorded_firmware = _text(data.get("firmware_version"), "firmware_version")
        snapshot = {
            "installation": _text(data.get("installation"), "installation"),
            "firmware_version": recorded_firmware or (newest.firmware_version if newest else None),
            "firmware_reported_at": newest.received_at
            if newest and not recorded_firmware
            else None,
            "firmware_source": "driver_recorded"
            if recorded_firmware
            else "device_report"
            if newest and newest.firmware_version
            else None,
            "software_version": current_app.config.get(
                "CAR_HEATER_SOFTWARE_VERSION", os.getenv("CAR_HEATER_SOFTWARE_VERSION", "1.0.0")
            ),
            "software_revision": current_app.config.get(
                "CAR_HEATER_SOFTWARE_REVISION", os.getenv("CAR_HEATER_SOFTWARE_REVISION")
            ),
        }
        attempt = current_app.ctrl.create_car_heater_session(
            trigger=trigger,
            observed_at=observed,
            requested_departure_at=departure,
            snapshot=snapshot,
            heating_duration_minutes=duration,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(asdict(attempt)), 201


@car_bp.get("/sessions/<session_id>")
@login_required
def session_detail(session_id):
    attempt = current_app.ctrl.get_car_heater_session(session_id)
    if attempt is None:
        return jsonify(error="Session not found"), 404
    payload = asdict(attempt)
    payload["photos"] = [asdict(p) for p in current_app.ctrl.list_car_heater_photos(session_id)]
    payload["observations"] = current_app.ctrl.list_car_heater_observations(session_id)
    execution = current_app.ctrl.get_car_heater_execution(session_id)
    if execution is not None:
        payload["execution"] = execution
    plan = current_app.ctrl.get_departure_plan(session_id)
    if plan is not None:
        payload["departure_plan"] = plan
    return jsonify(payload)


@car_bp.put("/sessions/<session_id>/outcome")
@login_required
def save_outcome(session_id):
    try:
        data = _data()
        if data.get("result") not in OUTCOMES:
            raise ValueError("Choose clear, wipers enough, partial scrape or full scrape")
        already_clear = data.get("already_clear_at_observation", False)
        if not isinstance(already_clear, bool):
            raise ValueError("already_clear_at_observation must be boolean")
        outcome = {
            "result": data["result"],
            "already_clear_at_observation": already_clear,
            "observed_at": _time(data.get("observed_at"), "observed_at"),
            "actual_departure_at": _time(data.get("actual_departure_at"), "actual_departure_at"),
            "frost_type": _text(data.get("frost_type"), "frost_type"),
            "other_windows": _text(data.get("other_windows"), "other_windows"),
            "interior_condition": _text(data.get("interior_condition"), "interior_condition"),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    attempt = current_app.ctrl.save_car_heater_outcome(session_id, outcome)
    if attempt is None:
        return jsonify(error="Session not found"), 404
    return jsonify(asdict(attempt))


@car_bp.get("/sessions/<session_id>/measurements")
@login_required
def session_measurements(session_id):
    review = build_session_review(current_app.ctrl, session_id)
    if review is None:
        return jsonify(error="Session not found"), 404
    return jsonify(review)
