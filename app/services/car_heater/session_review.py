"""Read-only measurement review of one heating session; never calls heater controls.

Raw telemetry is stored independently of sessions, so data between sessions is
never lost. A session's measurements are those inside its review window, which
is derived from the session's own evidence (heating start, finish, departure,
observations, photos) and returned with the basis used.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .sensor_readings import SENSOR_KEYS, iso_utc, sensor_max_age_s

logger = logging.getLogger(__name__)

LEAD = timedelta(minutes=30)
TAIL = timedelta(minutes=15)
DEFAULT_SPAN = timedelta(minutes=180)  # the heating limit, when no end is recorded
MAX_SPAN = timedelta(hours=6)
# Frames are stored by receipt time; a sample inside the window can arrive late.
RECEIPT_SLACK = timedelta(minutes=10)
FAILED_READS = ("missing", "invalid", "cached")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def review_window(session, plan, execution, photos, observations) -> dict[str, Any]:
    """Heating anchor and bounded [start, end] covering the session's evidence."""
    if execution and _parse(execution.get("issued_at")):
        anchor, source = _parse(execution["issued_at"]), "execution"
    elif plan and _parse(plan.get("planned_start_at")):
        anchor, source = _parse(plan["planned_start_at"]), "departure_plan"
    else:
        anchor, source = _parse(session.observed_at), "session"
    outcome = session.outcome or {}
    starts = [anchor, *(_parse(p.captured_at) for p in photos[:1])]
    starts += [_parse(o["started_at"]) for o in observations]
    ends = [
        _parse((execution or {}).get("finish_at")),
        *(_parse(m.get("at")) for m in (execution or {}).get("milestones", [])),
        _parse((plan or {}).get("finish_at")),
        _parse(session.requested_departure_at),
        _parse(outcome.get("actual_departure_at")),
        _parse(outcome.get("observed_at")),
        *(_parse(o.get("finished_at")) for o in observations),
        *(_parse(p.captured_at) for p in photos[-1:]),
    ]
    if session.heating_duration_minutes is not None:
        ends.append(anchor + timedelta(minutes=session.heating_duration_minutes))
    start = max(min(t for t in starts if t), anchor - MAX_SPAN)
    known_end = max((t for t in ends if t and t > anchor), default=None)
    end = min((known_end or anchor + DEFAULT_SPAN) + TAIL, anchor + MAX_SPAN)
    return {
        "start": _iso(start - LEAD),
        "end": _iso(end),
        "heating_anchor": _iso(anchor),
        "anchor_source": source,
        "end_recorded": known_end is not None,
    }


def sensor_history_series(
    frames, start: datetime, end: datetime
) -> dict[str, list[dict[str, Any]]]:
    series: dict[str, list[dict[str, Any]]] = {key: [] for key in SENSOR_KEYS}
    for frame in frames:
        for key in SENSOR_KEYS:
            state = getattr(frame, f"{key}_state")
            temperature = getattr(frame, f"{key}_temp_c")
            sampled_at = getattr(frame, f"{key}_sampled_at")
            if temperature is None and state in FAILED_READS:
                # A failed read is an explicit break in the line, never a value.
                sampled_at = frame.received_at
            elif temperature is None:
                continue
            if not start <= (_parse(sampled_at) or start - LEAD) <= end:
                continue
            point: dict[str, Any] = {"t": sampled_at, "temperature_c": temperature}
            if key == "cabin":
                point["humidity_pct"] = frame.cabin_humidity_pct
            point["state"] = state
            series[key].append(point)
    for points in series.values():
        points.sort(key=lambda p: p["t"])
    return series


# Candidate from the defrost study; display-only, never a control input.
INDICATOR_THRESHOLD_C = 3.0
INDICATOR_DWELL = timedelta(minutes=10)
# Fixed rather than CAR_HEATER_SENSOR_MAX_AGE_S, so candidate times recomputed
# from the permanent raw frames never shift when live settings change.
INDICATOR_MAX_GAP = timedelta(seconds=60)
INDICATOR_DEFINITION_VERSION = 1


def glass_indicator(frames, start: datetime, end: datetime) -> dict[str, Any]:
    """Experimental reference-glass +3 °C / ten-minute dwell, from stored frames.

    Only accepted on-time samples ("ok") count. A failed read (missing, invalid,
    or a cached repeat), a late sample (stale), a gap over INDICATOR_MAX_GAP,
    or a sample below the threshold ends the current dwell.
    """
    max_gap = INDICATOR_MAX_GAP
    events = []
    for frame in frames:
        state = frame.glass_reference_state
        sampled = _parse(frame.glass_reference_sampled_at)
        if state == "ok" and frame.glass_reference_temp_c is not None and sampled:
            if start <= sampled <= end:
                events.append((sampled, frame.glass_reference_temp_c, None))
        elif state in (*FAILED_READS, "stale"):
            at = sampled if state == "stale" and sampled else _parse(frame.received_at)
            if at and start <= at <= end:
                events.append((at, None, state))
    events.sort(key=lambda event: event[0])

    runs: list[dict[str, Any]] = []
    run = None
    previous = None
    valid = 0

    def close(at, reason):
        run.update(ended_at=iso_utc(at) if at else None, end_reason=reason)
        runs.append(run)

    for at, value, failure in events:
        if failure:
            if run:
                close(at, failure)
            run, previous = None, None
            continue
        valid += 1
        if run and previous and at - previous > max_gap:
            close(at, "gap")
            run = None
        previous = at
        if value < INDICATOR_THRESHOLD_C:
            if run:
                close(at, "fell_below")
            run = None
            continue
        if run is None:
            run = {"started_at": iso_utc(at), "reached_at": None, "_start": at}
        run["last_valid_at"] = iso_utc(at)
        if run["reached_at"] is None and at - run["_start"] >= INDICATOR_DWELL:
            run["reached_at"] = iso_utc(at)
    if run:
        close(None, "window_end")
    reached = [r["reached_at"] for r in runs if r["reached_at"]]
    return {
        "experimental": True,
        "definition_version": INDICATOR_DEFINITION_VERSION,
        "controls_heating": False,
        "sensor": "glass_reference",
        "threshold_c": INDICATOR_THRESHOLD_C,
        "dwell_s": int(INDICATOR_DWELL.total_seconds()),
        "max_gap_s": int(max_gap.total_seconds()),
        "status": "reached" if reached else "not_reached" if valid else "no_valid_evidence",
        "first_reached_at": reached[0] if reached else None,
        "valid_samples": valid,
        "runs": [
            {
                key: r[key]
                for key in ("started_at", "last_valid_at", "reached_at", "ended_at", "end_reason")
            }
            for r in runs
        ],
    }


def clearing(session, photos, observations) -> dict[str, Any]:
    """Interval-censored reading of the driver outcome; never an exact clearing time.

    An already-clear first observation only bounds clearing from above, and the
    shortened observation protocol is never a fully observed clearing trajectory.
    """
    outcome = session.outcome
    result = {
        "interpretation": "outcome_not_recorded" if outcome is None else "clearing_not_observed",
        "clear_no_later_than": None,
        "basis": None,
        "exact_clearing_time": None,
        "fully_observed_trajectory": False,
    }
    if outcome and outcome.get("already_clear_at_observation"):
        # The earliest evidence of the first observation gives the tightest bound.
        known = [(_parse(o["started_at"]), "observation_start") for o in observations]
        known += [(_parse(p.captured_at), "first_photo") for p in photos]
        known = [item for item in known if item[0]]
        if not known and _parse(outcome.get("observed_at")):
            known = [(_parse(outcome["observed_at"]), "outcome_observation")]
        bound, basis = min(known) if known else (None, None)
        result.update(
            interpretation="upper_bound",
            clear_no_later_than=_iso(bound) if bound else None,
            basis=basis,
        )
    return result


def _power_series(ctrl, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Shelly status rows only; a frame without Shelly data is a gap, never 0 W."""
    # Status timestamps are local ISO text; pad the text bounds across DST.
    helsinki = ZoneInfo("Europe/Helsinki")
    rows = ctrl.get_car_heater_status_between(
        str((start - timedelta(hours=1)).astimezone(helsinki)),
        str((end + timedelta(hours=1)).astimezone(helsinki)),
    )
    points = []
    for row in rows:
        at = _parse(str(row.timestamp))
        if at is None or not start <= at <= end:
            continue
        points.append(
            {
                "t": _iso(at),
                "power_w": row.instant_power_w,
                "relay_on": row.is_heater_on,
                "energy_total_wh": row.energy_total_wh,
            }
        )
    return sorted(points, key=lambda p: p["t"])


def _energy(power: list[dict[str, Any]]) -> dict[str, Any]:
    counters = [p["energy_total_wh"] for p in power if p["energy_total_wh"] is not None]
    first, last = (counters[0], counters[-1]) if counters else (None, None)
    used = round(last - first, 3) if counters and last >= first else None
    return {"first_wh": first, "last_wh": last, "used_wh": used, "source": "shelly_counter"}


def session_window(ctrl, session_id: str) -> dict[str, Any] | None:
    """The session's review window, or None for an unknown session."""
    session = ctrl.get_car_heater_session(session_id)
    if session is None:
        return None
    return review_window(
        session,
        ctrl.get_departure_plan(session_id),
        ctrl.get_car_heater_execution(session_id),
        ctrl.list_car_heater_photos(session_id),
        ctrl.list_car_heater_observations(session_id),
    )


def build_session_review(ctrl, session_id: str) -> dict[str, Any] | None:
    session = ctrl.get_car_heater_session(session_id)
    if session is None:
        return None
    plan = ctrl.get_departure_plan(session_id)
    execution = ctrl.get_car_heater_execution(session_id)
    photos = ctrl.list_car_heater_photos(session_id)
    observations = ctrl.list_car_heater_observations(session_id)
    window = review_window(session, plan, execution, photos, observations)
    start, end = _parse(window["start"]), _parse(window["end"])
    frames = ctrl.get_car_heater_sensor_frames_between(iso_utc(start), iso_utc(end + RECEIPT_SLACK))
    power = _power_series(ctrl, start, end)
    indicator = glass_indicator(frames, start, end)
    logger.debug(
        "Session review session=%s anchor=%s end_recorded=%s frames=%d power_points=%d "
        "indicator=%s",
        session_id,
        window["anchor_source"],
        window["end_recorded"],
        len(frames),
        len(power),
        indicator["status"],
    )
    return {
        "session_id": session_id,
        "window": window,
        "configuration": {
            "snapshot": session.snapshot,
            "execution": (execution or {}).get("configuration"),
            "reported_firmware_versions": sorted(
                {f.firmware_version for f in frames if f.firmware_version}
            ),
        },
        # Consecutive points further apart than this are drawn as a gap.
        "gap_after_s": sensor_max_age_s(),
        "series": {**sensor_history_series(frames, start, end), "power": power},
        "energy": _energy(power),
        "glass_indicator": indicator,
        "clearing": clearing(session, photos, observations),
    }
