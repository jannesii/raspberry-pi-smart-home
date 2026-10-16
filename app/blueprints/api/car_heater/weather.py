"""Authenticated, read-only weather evidence and collection health.

Readings and retrievals link to original replies by id; reply content is served
only by the explicit reply endpoint, never embedded in these JSON responses.
"""

import logging
from datetime import UTC, datetime, timedelta

from flask import Response, current_app, jsonify, request
from flask_login import login_required

from ....services.weather.observation_archive import STATION_LABELS
from ._blueprint import car_bp
from .sessions import _time

logger = logging.getLogger(__name__)

MAX_RANGE = timedelta(days=7)
# A healthy collector attempts every ten minutes; allow for one slow cycle.
STALE_AFTER = timedelta(minutes=25)


def _range():
    end = _time(request.args.get("end"), "end") or datetime.now(UTC).isoformat()
    start = (
        _time(request.args.get("start"), "start")
        or (datetime.fromisoformat(end) - timedelta(days=1)).isoformat()
    )
    span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    if span < timedelta(0) or span > MAX_RANGE:
        raise ValueError("Choose start before end, at most 7 days apart")
    return start, end


def _now():
    collector = getattr(current_app, "weather_collector", None)
    return collector.now() if collector is not None else datetime.now(UTC)


@car_bp.get("/weather/observations")
@login_required
def weather_observations():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    fmisid = request.args.get("fmisid") or None
    rows = current_app.ctrl.list_weather_observations(start, end, fmisid=fmisid)
    return jsonify(start=start, end=end, readings=rows)


@car_bp.get("/weather/retrievals")
@login_required
def weather_retrievals():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(
        start=start, end=end, retrievals=current_app.ctrl.list_weather_retrievals(start, end)
    )


@car_bp.get("/weather/health")
@login_required
def weather_health():
    collector = getattr(current_app, "weather_collector", None)
    sources = current_app.ctrl.get_weather_collection_health()
    now = _now()
    stations = collector.stations if collector is not None else tuple(sources)
    result = []
    for source_id in dict.fromkeys([*stations, *sources]):
        entry = sources.get(source_id) or {
            "source_id": source_id,
            "station_name": None,
            "last_attempt": None,
            "last_success": None,
            "last_failure": None,
            "consecutive_failures": 0,
        }
        entry["station_name"] = entry["station_name"] or STATION_LABELS.get(source_id)
        last = entry["last_attempt"]
        entry["stale"] = (
            last is None or now - datetime.fromisoformat(last["retrieved_at"]) > STALE_AFTER
        )
        if collector is not None and source_id in collector.archive_errors:
            entry["archive_error"] = collector.archive_errors[source_id]
        result.append(entry)
    return jsonify(
        checked_at=now.isoformat(),
        collector={
            "configured": collector is not None,
            "running": bool(collector is not None and collector.running),
            "interval_s": collector.interval_s if collector is not None else None,
            "window_s": int(collector.window.total_seconds()) if collector is not None else None,
        },
        sources=result,
    )


@car_bp.get("/weather/replies/<int:reply_id>")
@login_required
def weather_reply(reply_id):
    try:
        reply = current_app.ctrl.get_weather_reply(reply_id)
    except ValueError:
        return jsonify(error="Stored reply failed its integrity check"), 500
    if reply is None:
        return jsonify(error="Reply not found"), 404
    logger.debug("Serving original weather reply id=%s bytes=%d", reply_id, len(reply["content"]))
    response = Response(reply["content"], mimetype="application/xml")
    response.headers["Content-Disposition"] = f'attachment; filename="fmi-reply-{reply_id}.xml"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Content-SHA256"] = reply["sha256"]
    response.headers["Cache-Control"] = "private, no-store"
    return response
