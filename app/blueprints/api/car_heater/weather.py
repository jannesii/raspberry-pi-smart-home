"""Authenticated, read-only weather evidence and collection health.

Readings and retrievals link to original replies by id; reply content is served
only by the explicit reply endpoint, never embedded in these JSON responses.
"""

import csv
import io
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Response, current_app, jsonify, request, url_for
from flask_login import login_required

from ....services.car_heater.session_review import session_window
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
MAX_ID = 2**63 - 1
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
    now = _now()
    stations = _station_coverage(
        datetime.fromisoformat(start), datetime.fromisoformat(end), now, request.args.get("fmisid")
    )
    return jsonify(start=start, end=end, checked_at=now.isoformat(), stations=stations)


def _station_coverage(start, end, now, wanted=None):
    """Segments per configured or previously collected station, up to ``now``."""
    collector = getattr(current_app, "weather_collector", None)
    progress = current_app.ctrl.list_weather_collection_progress("observation")
    configured = collector.stations if collector is not None else ()
    lo, hi = start, min(end, now)
    stations = []
    for source_id in dict.fromkeys([*configured, *progress]):
        if wanted and source_id != wanted:
            continue
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
    return stations


def _health_state(entry):
    last = entry["last_attempt"]
    if last is None:
        return "not_started"
    if entry["stale"]:
        return "stale"
    return "collecting" if last["status"] == "ok" else "failing"


def _brief_health(entry):
    """Collection state without the full retrieval summaries."""
    last, success = entry["last_attempt"], entry["last_success"]
    failure = entry["last_failure"]
    return {
        "state": _health_state(entry),
        "last_attempt_at": last["retrieved_at"] if last else None,
        "last_success_at": success["retrieved_at"] if success else None,
        "last_failure_at": failure["retrieved_at"] if failure else None,
        "last_error": failure["error"] if failure else None,
        "last_http_status": failure["http_status"] if failure else None,
        "consecutive_failures": entry["consecutive_failures"],
        "archive_error": entry.get("archive_error"),
    }


@car_bp.get("/sessions/<session_id>/weather")
@login_required
def session_weather(session_id):
    """Read-only weather evidence around one session's review window.

    Weather rows are continuous and belong to no session; this reports which
    archived evidence covers the window, and never changes the session.
    """
    window = session_window(current_app.ctrl, session_id)
    if window is None:
        logger.debug("Session weather requested for unknown session=%s", session_id)
        return jsonify(error="Session not found"), 404
    ctrl = current_app.ctrl
    now = _now()
    lo, hi = datetime.fromisoformat(window["start"]), datetime.fromisoformat(window["end"])
    until = max(lo, min(hi, now))
    collector = getattr(current_app, "weather_collector", None)
    step, limit = _observation_settings(collector)
    stations = _station_coverage(lo, hi, now)
    health = {
        e["source_id"]: e
        for e in _source_health(
            "observation", [s["source_id"] for s in stations], collector, STALE_AFTER, now
        )
    }
    for entry in stations:
        source_id = entry["source_id"]
        rows = ctrl.list_weather_observations(window["start"], window["end"], fmisid=source_id)
        current = [r for r in rows if r["current"]]
        entry["station_name"] = next(
            (r["station_name"] for r in rows if r["station_name"]), None
        ) or STATION_LABELS.get(source_id)
        entry["readings"] = {
            "current": len(current),
            "revised": sum(1 for r in current if r["revision"] > 1),
            "backfilled": sum(1 for r in current if r["backfill"]),
        }
        entry["health"] = _brief_health(health[source_id])
        entry["replies"] = ctrl.list_weather_observation_replies(
            source_id, window["start"], until.isoformat(), step_s=step
        )
    logger.debug(
        "Session weather session=%s window=%s..%s stations=%d gaps=%d",
        session_id,
        window["start"],
        window["end"],
        len(stations),
        sum(1 for st in stations for seg in st["segments"] if seg["kind"] != "collected"),
    )
    return jsonify(
        session_id=session_id,
        window=window,
        checked_at=now.isoformat(),
        checked_until=until.isoformat(),
        collector={
            "configured": collector is not None,
            "running": bool(collector is not None and collector.running),
            "repair_limit_s": int(limit.total_seconds()),
        },
        stations=stations,
        forecast=_session_forecast(window, until, now),
        exports={
            name: {
                fmt: url_for(
                    ".weather_export",
                    kind=kind,
                    format=fmt,
                    start=window["start"],
                    end=window["end"],
                )
                for fmt in ("csv", "json")
            }
            for name, kind in (
                ("observations", "observation"),
                ("forecasts", "forecast"),
                ("coverage", "coverage"),
            )
        },
    )


def _session_forecast(window, until, now):
    forecaster = getattr(current_app, "weather_forecast_collector", None)
    source_id = forecaster.source_id if forecaster is not None else None
    if source_id is None:
        return {"configured": False, "running": False, "source_id": None}
    ctrl = current_app.ctrl
    interval = forecaster.interval_s
    stale_after = timedelta(seconds=interval * STALE_AFTER_INTERVALS)
    health = next(
        e
        for e in _source_health("forecast", (source_id,), forecaster, stale_after, now)
        if e["source_id"] == source_id
    )
    point = ForecastPoint.from_id(source_id)
    return {
        "configured": True,
        "running": bool(forecaster.running),
        "source_id": source_id,
        "requested_location": point.as_dict() if point else None,
        "health": _brief_health(health),
        # What the archive held when heating began, then what arrived during the window.
        "held_at_anchor": ctrl.list_weather_forecast_versions_held(
            source_id, window["heating_anchor"]
        ),
        "received_in_window": ctrl.list_weather_forecast_versions(
            window["start"], window["end"], source_id=source_id
        ),
        "unobserved": _unchecked(source_id, datetime.fromisoformat(window["start"]), until, now),
    }


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


# Bounded to the database integer range, so nothing outside it is ever looked up.
@car_bp.get(f"/weather/replies/<int(min=1, max={MAX_ID}):reply_id>")
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


@car_bp.get(f"/weather/forecasts/<int(min=1, max={MAX_ID}):version_id>")
@login_required
def weather_forecast(version_id):
    """One received version with its complete returned hourly horizon."""
    version = current_app.ctrl.get_weather_forecast_version(version_id)
    if version is None:
        return jsonify(error="Forecast version not found"), 404
    return jsonify(version)


# Bounds keep one export comfortably within a Pi's memory: two stations of
# ten-minute readings for a week, or every forecast version valid in two days.
EXPORT_RANGE = {
    "observation": timedelta(days=7),
    "forecast": timedelta(days=2),
    "coverage": timedelta(days=7),
}
OBSERVATION_COLUMNS = (
    "fmisid",
    "station_name",
    "station_pos",
    "parameter",
    "observed_at",
    "revision",
    "current",
    "status",
    "value",
    "source_value",
    "unit",
    "period",
    "property_href",
    "retrieval_id",
    "retrieved_at",
    "purpose",
    "backfill",
    "reply_id",
)
FORECAST_COLUMNS = (
    "version_id",
    "source_id",
    "requested_lat",
    "requested_lon",
    "location_name",
    "location_pos",
    "procedure",
    "analysis_time",
    "result_time",
    "revision",
    "current",
    "first_retrieval_id",
    "first_retrieved_at",
    "first_reply_id",
    "receipts",
    "last_received_at",
    "parameter",
    "valid_at",
    "status",
    "value",
    "source_value",
    "unit",
    "period",
    "statistic",
    "property_href",
)
COVERAGE_COLUMNS = ("source_kind", "source_id", "start", "end", "state", "failed_attempts")
HELSINKI = ZoneInfo("Europe/Helsinki")


def _export_range(kind):
    """ISO ``start``/``end``, or inclusive Helsinki ``start_date``/``end_date``."""
    limit = EXPORT_RANGE[kind]
    if request.args.get("start_date") or request.args.get("end_date"):
        try:
            first = date.fromisoformat(request.args.get("start_date") or "")
            last = date.fromisoformat(request.args.get("end_date") or first.isoformat())
        except ValueError:
            raise ValueError("Dates must be YYYY-MM-DD") from None
        if last < first:
            raise ValueError("Choose a start date on or before the end date")
        # Counted in calendar days: a week across a clock change is 167 or 169 hours.
        if (last - first).days >= limit.days:
            raise ValueError(f"Choose at most {limit.days} days")
        start = datetime.combine(first, datetime.min.time(), HELSINKI).astimezone(UTC)
        end = datetime.combine(last + timedelta(days=1), datetime.min.time(), HELSINKI)
        return start.isoformat(), end.astimezone(UTC).isoformat()
    end = _time(request.args.get("end"), "end") or _now().isoformat()
    start = (
        _time(request.args.get("start"), "start")
        or (datetime.fromisoformat(end) - timedelta(days=1)).isoformat()
    )
    span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    if span < timedelta(0) or span > limit:
        raise ValueError(f"Choose start before end, at most {limit.days} days apart")
    return start, end


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Spreadsheet formula guard for text cells; numbers keep their sign.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text


def _csv(rows, columns):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return out.getvalue()


@car_bp.get("/weather/export")
@login_required
def weather_export():
    """Archived evidence in a bounded range as CSV or JSON, never reply content."""
    kind = request.args.get("kind") or "observation"
    fmt = request.args.get("format") or "csv"
    try:
        if kind not in EXPORT_RANGE:
            raise ValueError("kind must be observation, forecast or coverage")
        if fmt not in ("csv", "json"):
            raise ValueError("format must be csv or json")
        start, end = _export_range(kind)
        as_of = _time(request.args.get("as_of"), "as_of")
    except ValueError as exc:
        logger.debug("Weather export refused kind=%s format=%s: %s", kind, fmt, exc)
        return jsonify(error=str(exc)), 400
    ctrl = current_app.ctrl
    now = _now()
    lo, hi = datetime.fromisoformat(start), datetime.fromisoformat(end)
    forecaster = getattr(current_app, "weather_forecast_collector", None)
    source_id = request.args.get("source_id") or (forecaster.source_id if forecaster else None)
    fmisid = request.args.get("fmisid") or None
    # Coverage is judged from the archive as it is now, even for an as_of export.
    payload = {
        "kind": kind,
        "start": start,
        "end": end,
        "as_of": as_of,
        "checked_at": now.isoformat(),
    }
    if kind == "observation":
        rows = ctrl.list_weather_observations(start, end, fmisid=fmisid, as_of_iso=as_of)
        columns = OBSERVATION_COLUMNS
        payload.update(fmisid=fmisid, coverage=_station_coverage(lo, hi, now, fmisid))
    elif kind == "forecast":
        versions, rows = ctrl.export_weather_forecast_values(
            start, end, source_id=source_id, as_of_iso=as_of
        )
        columns = FORECAST_COLUMNS
        payload.update(
            source_id=source_id,
            versions=versions,
            # Checks, not valid times: stretches in the range without a successful check.
            unobserved=_unchecked(source_id, lo, hi, now),
        )
    else:
        rows = [
            {"source_kind": "observation", "source_id": station["source_id"], **segment}
            for station in _station_coverage(lo, hi, now, fmisid)
            for segment in station["segments"]
        ]
        rows += [
            {"source_kind": "forecast", "source_id": source_id, "kind": "unchecked", **gap}
            for gap in _unchecked(source_id, lo, hi, now) or []
        ]
        rows = [{**row, "state": row.pop("kind")} for row in rows]
        columns = COVERAGE_COLUMNS
    logger.debug("Weather export kind=%s format=%s rows=%d", kind, fmt, len(rows))
    # Name the file by the Helsinki dates the range covers.
    first = lo.astimezone(HELSINKI).date()
    last = max(lo, hi - timedelta(microseconds=1)).astimezone(HELSINKI).date()
    stem = f"weather-{kind}{'' if kind == 'coverage' else 's'}-{first}-{last}"
    if fmt == "json":
        response = jsonify(generated_at=now.isoformat(), **payload, rows=rows)
    else:
        response = Response(_csv(rows, columns), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{stem}.{fmt}"'
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _unchecked(source_id, start, end, now):
    """Forecast stretches in [start, min(end, now)] without a successful check."""
    if not source_id:
        return None
    forecaster = getattr(current_app, "weather_forecast_collector", None)
    interval = forecaster.interval_s if forecaster is not None else FORECAST_INTERVAL_S
    until = max(start, min(end, now))
    return current_app.ctrl.list_weather_forecast_unobserved(
        source_id, start.isoformat(), until.isoformat(), max_gap_s=interval * UNOBSERVED_AFTER
    )
