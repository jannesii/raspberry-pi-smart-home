"""Authenticated, read-only weather evidence and collection health.

Readings and retrievals link to original replies by id; reply content is served
only by the explicit reply endpoint, never embedded in these JSON responses.
"""

import logging
from datetime import UTC, datetime, timedelta

from flask import Response, current_app, jsonify, request
from flask_login import login_required

from ....services.weather.forecast_archive import (
    INTERVAL_S as FORECAST_INTERVAL_S,
    STALE_AFTER_INTERVALS,
    UNOBSERVED_AFTER,
    ForecastPoint,
)
from ....services.weather.observation_archive import (
    INTERVAL_S as OBSERVATION_INTERVAL_S,
    PENDING_KINDS,
    REPAIR_LIMIT,
    STATION_LABELS,
)
from ._blueprint import car_bp
from .sessions import _time

logger = logging.getLogger(__name__)

MAX_RANGE = timedelta(days=7)
KINDS = ("observation", "forecast")
# A healthy collector attempts every ten minutes; allow for one slow cycle.
STALE_AFTER = timedelta(minutes=25)
# Health summarises observation gaps this far back; /weather/coverage shows any range.
HISTORY_LOOKBACK = timedelta(days=30)
RECENT_GAPS = 20


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
    for name in ("weather_collector", "weather_forecast_collector"):
        collector = getattr(current_app, name, None)
        if collector is not None:
            return collector.now()
    return datetime.now(UTC)


@car_bp.get("/weather/observations")
@login_required
def weather_observations():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    try:
        as_of = _time(request.args.get("as_of"), "as_of")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    fmisid = request.args.get("fmisid") or None
    rows = current_app.ctrl.list_weather_observations(start, end, fmisid=fmisid, as_of_iso=as_of)
    return jsonify(start=start, end=end, as_of=as_of, readings=rows)


@car_bp.get("/weather/retrievals")
@login_required
def weather_retrievals():
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    kind = request.args.get("kind") or "observation"
    if kind not in KINDS:
        return jsonify(error="kind must be observation or forecast"), 400
    return jsonify(
        start=start,
        end=end,
        kind=kind,
        retrievals=current_app.ctrl.list_weather_retrievals(start, end, kind=kind),
    )


@car_bp.get("/weather/health")
@login_required
def weather_health():
    collector = getattr(current_app, "weather_collector", None)
    forecaster = getattr(current_app, "weather_forecast_collector", None)
    now = _now()
    stations = collector.stations if collector is not None else ()
    result = _source_health("observation", stations, collector, STALE_AFTER, now)
    progress = current_app.ctrl.list_weather_collection_progress("observation")
    for entry in result:
        entry["station_name"] = entry["station_name"] or STATION_LABELS.get(entry["source_id"])
        entry["history"] = _observation_history(
            entry["source_id"], progress.get(entry["source_id"]), collector, now
        )
    points = (forecaster.source_id,) if forecaster is not None else ()
    interval = forecaster.interval_s if forecaster is not None else FORECAST_INTERVAL_S
    forecast = _source_health(
        "forecast", points, forecaster, timedelta(seconds=interval * STALE_AFTER_INTERVALS), now
    )
    day_ago = (now - timedelta(days=1)).isoformat()
    for entry in forecast:
        entry.pop("station_name", None)
        point = ForecastPoint.from_id(entry["source_id"])
        entry["requested_location"] = point.as_dict() if point else None
        gaps = current_app.ctrl.list_weather_forecast_unobserved(
            entry["source_id"], day_ago, now.isoformat(), max_gap_s=interval * UNOBSERVED_AFTER
        )
        entry["unobserved_24h"] = {"count": len(gaps), "latest": gaps[-1] if gaps else None}
    return jsonify(
        checked_at=now.isoformat(),
        collector={
            "configured": collector is not None,
            "running": bool(collector is not None and collector.running),
            "interval_s": collector.interval_s if collector is not None else None,
            "window_s": int(collector.window.total_seconds()) if collector is not None else None,
        },
        sources=result,
        forecast={
            "collector": {
                "configured": forecaster is not None,
                "running": bool(forecaster is not None and forecaster.running),
                "interval_s": interval,
            },
            "sources": forecast,
        },
    )


def _observation_settings(collector):
    if collector is None:
        return OBSERVATION_INTERVAL_S, REPAIR_LIMIT
    return collector.interval_s, collector.repair_limit


def _coverage(source_id, start, end, collector, now):
    step, limit = _observation_settings(collector)
    return current_app.ctrl.get_weather_observation_coverage(
        source_id,
        start.isoformat(),
        end.isoformat(),
        step_s=step,
        repair_from_iso=(now - limit).isoformat(),
    )


def _observation_history(source_id, progress, collector, now):
    """Durable initialization progress and recent gaps for one station."""
    if progress is None:
        return None
    step, limit = _observation_settings(collector)
    # Source times up to the current slot; the slot itself is still being collected.
    until = datetime.fromtimestamp(now.timestamp() - now.timestamp() % step, UTC)
    since = max(datetime.fromisoformat(progress["history_start"]), now - HISTORY_LOOKBACK)
    gaps = [
        s for s in _coverage(source_id, since, until, collector, now) if s["kind"] != "collected"
    ]
    pending = [g for g in gaps if g["kind"] in PENDING_KINDS]
    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap["kind"]] = counts.get(gap["kind"], 0) + 1
    return {
        "origin": progress["origin"],
        "history_start": progress["history_start"],
        "created_at": progress["created_at"],
        "initialized_at": progress["initialized_at"],
        "last_repair_at": progress["last_repair_at"],
        "repair_limit_s": int(limit.total_seconds()),
        "checked_since": since.isoformat(),
        "pending_repair": {
            "count": len(pending),
            "oldest": pending[0]["start"] if pending else None,
        },
        "gap_counts": counts,
        "recent_gaps": gaps[-RECENT_GAPS:][::-1],
    }


@car_bp.get("/weather/coverage")
@login_required
def weather_coverage():
    """Per-station collected stretches and gaps, derived from retrieval history."""
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    collector = getattr(current_app, "weather_collector", None)
    now = _now()
    progress = current_app.ctrl.list_weather_collection_progress("observation")
    wanted = request.args.get("fmisid") or None
    configured = collector.stations if collector is not None else ()
    stations = []
    for source_id in dict.fromkeys([*configured, *progress]):
        if wanted and source_id != wanted:
            continue
        lo, hi = datetime.fromisoformat(start), min(datetime.fromisoformat(end), now)
        segments = []
        entry = progress.get(source_id)
        history_start = datetime.fromisoformat(entry["history_start"]) if entry else hi
        if lo < min(history_start, hi):
            segments.append(
                {
                    "start": lo.isoformat(),
                    "end": min(history_start, hi).isoformat(),
                    "kind": "before_history",
                }
            )
        segments += _coverage(source_id, max(lo, history_start), hi, collector, now)
        stations.append(
            {
                "source_id": source_id,
                "history_start": entry["history_start"] if entry else None,
                "segments": segments,
            }
        )
    return jsonify(start=start, end=end, checked_at=now.isoformat(), stations=stations)


def _source_health(kind, configured, collector, stale_after, now):
    """Health per configured or previously collected source of one kind."""
    sources = current_app.ctrl.get_weather_collection_health(kind)
    result = []
    for source_id in dict.fromkeys([*configured, *sources]):
        entry = sources.get(source_id) or {
            "source_id": source_id,
            "station_name": None,
            "last_attempt": None,
            "last_success": None,
            "last_failure": None,
            "consecutive_failures": 0,
        }
        last = entry["last_attempt"]
        entry["stale"] = (
            last is None or now - datetime.fromisoformat(last["retrieved_at"]) > stale_after
        )
        if collector is not None and source_id in collector.archive_errors:
            entry["archive_error"] = collector.archive_errors[source_id]
        result.append(entry)
    return result


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


@car_bp.get("/weather/forecasts")
@login_required
def weather_forecasts():
    """Forecast versions received in the range, without their values."""
    try:
        start, end = _range()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    forecaster = getattr(current_app, "weather_forecast_collector", None)
    source_id = request.args.get("source_id") or None
    rows = current_app.ctrl.list_weather_forecast_versions(start, end, source_id=source_id)
    coverage_source = source_id or (forecaster.source_id if forecaster is not None else None)
    interval = forecaster.interval_s if forecaster is not None else FORECAST_INTERVAL_S
    unobserved = None
    if coverage_source:
        until = min(datetime.fromisoformat(end), _now()).isoformat()
        unobserved = current_app.ctrl.list_weather_forecast_unobserved(
            coverage_source, start, max(start, until), max_gap_s=interval * UNOBSERVED_AFTER
        )
    return jsonify(
        start=start,
        end=end,
        source_id=coverage_source,
        interval_s=interval,
        versions=rows,
        unobserved=unobserved,
    )


@car_bp.get("/weather/forecasts/<int:version_id>")
@login_required
def weather_forecast(version_id):
    """One received version with its complete returned hourly horizon."""
    version = current_app.ctrl.get_weather_forecast_version(version_id)
    if version is None:
        return jsonify(error="Forecast version not found"), 404
    return jsonify(version)
