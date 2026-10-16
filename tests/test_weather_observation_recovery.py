"""Observation history initialization, gap repair and coverage through public seams.

The FMI HTTP boundary and clock are controlled: a fake station answers each
requested window from its own series. Parsing, persistence, recovery planning
and the authenticated endpoints are the real ones, on an isolated database.
"""

from datetime import UTC, datetime, timedelta

import pytest
import test_car_heater_sessions as evidence
import test_departure_heating
from test_weather_observation_archive import AIRPORT, PELMAA, Response, fmi_reply

from app.core import Controller
from app.services.weather.fmi_wfs import iso_z

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
departure = test_departure_heating.departure

START = datetime(2027, 1, 15, 3, 0, tzinfo=UTC)
STEP = timedelta(minutes=10)


def _parse(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def temperature(fmisid, t):
    """A distinct, reproducible source value per station and time."""
    base = -5.0 if fmisid == PELMAA else -8.0
    return f"{base - (t.hour * 60 + t.minute) / 1000:.3f}"


class StationFMI:
    """Answers every requested window with the station's t2m series.

    ``failing`` stations raise; ``empty`` stations answer with no series;
    ``overrides`` replace one source value (a provider correction).
    """

    def __init__(self):
        self.requests = []
        self.failing = {}
        self.empty = set()
        self.overrides = {}
        self.on_request = None
        # Optional predicate(params) -> failure for selected windows only.
        self.fail_window = None
        # Answer with points newest first, as a reordered reply would.
        self.reverse = False

    def get(self, url, params=None, timeout=None):
        self.requests.append(dict(params))
        if self.on_request:
            self.on_request(params)
        fmisid = params["fmisid"]
        failure = self.fail_window(params) if self.fail_window else None
        if failure is None and fmisid in self.failing:
            failure = self.failing[fmisid]
        if failure is not None:
            if isinstance(failure, Exception):
                raise failure
            return failure
        if fmisid in self.empty:
            return Response(200, fmi_reply(fmisid, {}))
        start, end = _parse(params["starttime"]), _parse(params["endtime"])
        points, t = [], start
        while t <= end:
            points.append((t, self.overrides.get((fmisid, t), temperature(fmisid, t))))
            t += STEP
        if self.reverse:
            points.reverse()
        return Response(200, fmi_reply(fmisid, {"t2m": points}))

    def windows(self, fmisid=None, since=0):
        return [
            (_parse(r["starttime"]), _parse(r["endtime"]))
            for r in self.requests[since:]
            if fmisid is None or r["fmisid"] == fmisid
        ]


@pytest.fixture
def station(session_app):
    from app.services.weather.observation_archive import ObservationCollector

    fmi = StationFMI()
    clock = [START.timestamp()]

    def build():
        collector = ObservationCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
        session_app.weather_collector = collector
        return collector

    yield build, fmi, clock


def readings(client, start, end, **query):
    response = client.get(
        "/api/car_heater/weather/observations",
        query_string={"start": start.isoformat(), "end": end.isoformat(), **query},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["readings"]


def readings_span(client, start, end):
    """Readings over any span, through API ranges of at most seven days."""
    rows, lo = [], start
    while lo <= end:
        hi = min(lo + timedelta(days=6), end)
        rows += [r for r in readings(client, lo, hi) if _parse(r["observed_at"]) != hi or hi == end]
        lo = hi + STEP if hi == end else hi
    return rows


def current_times(rows, fmisid):
    return sorted(
        {_parse(r["observed_at"]) for r in rows if r["fmisid"] == fmisid and r["current"]}
    )


def health(client):
    response = client.get("/api/car_heater/weather/health")
    assert response.status_code == 200
    return {s["source_id"]: s for s in response.get_json()["sources"]}


def grid(start, end):
    """Every ten-minute source time in [start, end]."""
    times, t = [], start
    while t <= end:
        times.append(t)
        t += STEP
    return times


def test_first_initialization_fetches_the_preceding_24_hours_for_both_stations(station, client):
    build, fmi, _clock = station
    collector = build()
    assert collector.run_pending() is True

    for fmisid in (PELMAA, AIRPORT):
        windows = fmi.windows(fmisid)
        # The live two-hour window comes first, then bounded aligned history requests.
        assert windows[0] == (START - timedelta(hours=2), START)
        history = windows[1:]
        assert history, "no history requested"
        for lo, hi in history:
            assert hi - lo <= timedelta(hours=6)
            assert lo.minute % 10 == 0 and hi.minute % 10 == 0
            assert lo >= START - timedelta(hours=24)
        assert min(lo for lo, _ in history) == START - timedelta(hours=24)

    rows = readings(client, START - timedelta(days=2), START)
    for fmisid in (PELMAA, AIRPORT):
        assert current_times(rows, fmisid) == grid(START - timedelta(hours=24), START)
    late = [r for r in rows if _parse(r["observed_at"]) < START - timedelta(hours=2)]
    # Backfilled values carry their real retrieval time and an explicit purpose.
    assert {r["purpose"] for r in late} == {"initialization"}
    assert all(r["backfill"] for r in late)
    live = [r for r in rows if _parse(r["observed_at"]) == START]
    assert {r["purpose"] for r in live} == {"live"}
    assert not any(r["backfill"] for r in live)

    state = health(client)
    for fmisid in (PELMAA, AIRPORT):
        history = state[fmisid]["history"]
        assert history["origin"] == "new"
        assert history["history_start"] == (START - timedelta(hours=24)).isoformat()
        assert history["initialized_at"] == START.isoformat()
        assert history["pending_repair"]["count"] == 0
    # Observation recovery never requests or invents forecast runs (#25 owns those gaps).
    forecasts = client.get(
        "/api/car_heater/weather/retrievals",
        query_string={
            "kind": "forecast",
            "start": (START - timedelta(days=2)).isoformat(),
            "end": START.isoformat(),
        },
    ).get_json()["retrievals"]
    assert forecasts == []
    assert {r["storedquery_id"] for r in fmi.requests} == {
        "fmi::observations::weather::timevaluepair"
    }


def restart(session_app, build):
    session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
    session_app.ctrl._sa_engine = session_app.extensions["session_engine"]
    return build()


def test_partial_failure_resumes_after_restart_without_reinitializing(station, session_app, client):
    import requests

    build, fmi, clock = station
    live_start = iso_z(START - timedelta(hours=2))

    def history_times_out(params):
        if params["fmisid"] == PELMAA and params["starttime"] != live_start:
            return requests.Timeout("slow")
        return None

    fmi.fail_window = history_times_out
    build().run_pending()
    pelmaa = fmi.windows(PELMAA)
    # Live succeeded; the first history window failed and the rest waited.
    assert len(pelmaa) == 2
    state = health(client)
    assert state[AIRPORT]["history"]["initialized_at"] == START.isoformat()
    history = state[PELMAA]["history"]
    assert history["initialized_at"] is None
    assert history["pending_repair"]["count"] >= 1
    assert history["pending_repair"]["oldest"] == (START - timedelta(hours=24)).isoformat()
    kinds = {g["kind"] for g in history["recent_gaps"]}
    # The attempted window failed; the rest was never requested. Neither is recovered.
    assert kinds == {"failed", "not_collected"}
    rows = readings(client, START - timedelta(days=2), START)
    assert current_times(rows, PELMAA) == grid(START - timedelta(hours=2), START)
    assert current_times(rows, AIRPORT) == grid(START - timedelta(hours=24), START)

    # Restart in the next slot with the source healthy again.
    fmi.fail_window = None
    clock[0] = (START + STEP).timestamp()
    since = len(fmi.requests)
    restart(session_app, build).run_pending()
    # The other station resumes with live collection only: no reinitialization.
    assert fmi.windows(AIRPORT, since) == [(START + STEP - timedelta(hours=2), START + STEP)]
    resumed = fmi.windows(PELMAA, since)[1:]
    assert resumed and min(lo for lo, _ in resumed) == START - timedelta(hours=24)
    state = health(client)
    assert state[AIRPORT]["history"]["initialized_at"] == START.isoformat()
    assert state[PELMAA]["history"]["initialized_at"] == (START + STEP).isoformat()
    assert state[PELMAA]["history"]["history_start"] == (START - timedelta(hours=24)).isoformat()
    assert state[PELMAA]["history"]["pending_repair"]["count"] == 0
    rows = readings(client, START - timedelta(days=2), START + STEP)
    assert current_times(rows, PELMAA) == grid(START - timedelta(hours=24), START + STEP)
    # Retries and overlap never duplicate a reading; the failed attempt stays in history.
    keys = [(r["fmisid"], r["observed_at"]) for r in rows]
    assert len(keys) == len(set(keys))
    attempts = client.get(
        "/api/car_heater/weather/retrievals",
        query_string={"start": START.isoformat(), "end": (START + STEP).isoformat()},
    ).get_json()["retrievals"]
    failed = [r for r in attempts if r["status"] != "ok"]
    assert [(r["source_id"], r["purpose"], r["error"]) for r in failed] == [
        (PELMAA, "initialization", "timeout")
    ]


# Local midnight (EET), three days before Helsinki moves to summer time at
# 2027-03-28T01:00Z: recovery windows and readings stay continuous in UTC.
SPRING = datetime(2027, 3, 25, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize("days", [3, 7, 9])
def test_outage_recovery_stops_at_the_seven_day_limit_and_keeps_older_gaps_visible(
    station, session_app, client, days
):
    build, fmi, clock = station
    clock[0] = SPRING.timestamp()
    build().run_pending()
    back = SPRING + timedelta(days=days)
    clock[0] = back.timestamp()
    since = len(fmi.requests)
    collector = restart(session_app, build)
    for _ in range(8):
        collector.run_pending()
        clock[0] += STEP.total_seconds()
    now = datetime.fromtimestamp(clock[0], UTC) - STEP
    limit = back - timedelta(days=7)

    for fmisid in (PELMAA, AIRPORT):
        windows = fmi.windows(fmisid, since)
        for lo, hi in windows:
            assert hi - lo <= timedelta(hours=6)
            # Never beyond the repair limit of the slot that asked.
            assert lo >= limit
            assert lo.minute % 10 == 0 and hi.minute % 10 == 0
            assert (
                (lo.hour % 6 == 0 and lo.minute == 0)
                or lo == limit
                or hi - lo == timedelta(hours=2)
            )
    recovered_from = max(SPRING, limit)
    rows = readings_span(client, recovered_from, now)
    for fmisid in (PELMAA, AIRPORT):
        assert current_times(rows, fmisid) == grid(recovered_from, now)
    repaired = [r for r in rows if SPRING < _parse(r["observed_at"]) < back - timedelta(hours=2)]
    assert repaired and {r["purpose"] for r in repaired} == {"repair"}
    # Values recovered across the DST change keep their own UTC source time.
    dst = _parse("2027-03-28T01:00:00Z")
    at_dst = [r for r in rows if r["fmisid"] == PELMAA and _parse(r["observed_at"]) == dst]
    assert [r["value"] for r in at_dst] == [float(temperature(PELMAA, dst))]

    state = health(client)
    for fmisid in (PELMAA, AIRPORT):
        history = state[fmisid]["history"]
        assert history["pending_repair"]["count"] == 0
        unrepaired = [g for g in history["recent_gaps"] if g["kind"] == "unrepaired"]
        if days > 7:
            assert unrepaired == [
                {
                    "start": (SPRING + STEP).isoformat(),
                    "end": limit.isoformat(),
                    "kind": "unrepaired",
                }
            ]
            assert history["gap_counts"] == {"unrepaired": 1}
            assert not readings(client, SPRING + STEP, limit - STEP, fmisid=fmisid)
        else:
            assert unrepaired == []
            assert history["gap_counts"] == {}


def test_backfill_keeps_retrieval_times_and_never_rewrites_what_an_earlier_decision_saw(
    station, session_app, client
):
    build, fmi, clock = station
    build().run_pending()
    decided_at = START + timedelta(minutes=5)
    corrected = START - timedelta(minutes=30)
    # Down for five hours; meanwhile FMI corrects a value already archived live.
    back = START + timedelta(hours=5)
    fmi.overrides[(PELMAA, corrected)] = "-1.234"
    fmi.reverse = True
    clock[0] = back.timestamp()
    restart(session_app, build).run_pending()

    rows = readings(client, START - timedelta(hours=1), back)
    history = [r for r in rows if r["fmisid"] == PELMAA and _parse(r["observed_at"]) == corrected]
    # The original live value stays beside the later correction, with its own retrieval.
    assert [(r["revision"], r["value"], r["purpose"], r["current"]) for r in history] == [
        (1, float(temperature(PELMAA, corrected)), "live", False),
        (2, -1.234, "repair", True),
    ]
    assert _parse(history[0]["retrieved_at"]) == START
    assert _parse(history[1]["retrieved_at"]) == back
    late = [r for r in rows if START < _parse(r["observed_at"]) < back - timedelta(hours=2)]
    assert late and all(r["backfill"] and _parse(r["retrieved_at"]) == back for r in late)
    # Reordered points still land at their own source times.
    assert current_times(rows, PELMAA) == grid(START - timedelta(hours=1), back)

    # As known at an earlier decision: no backfilled values, no later correction.
    known = readings(client, START - timedelta(hours=1), back, as_of=decided_at.isoformat())
    assert current_times(known, PELMAA) == grid(START - timedelta(hours=1), START)
    seen = [r for r in known if r["fmisid"] == PELMAA and _parse(r["observed_at"]) == corrected]
    assert [(r["revision"], r["value"], r["current"]) for r in seen] == [
        (1, float(temperature(PELMAA, corrected)), True)
    ]
    assert not any(r["backfill"] for r in known)
    bad = client.get("/api/car_heater/weather/observations", query_string={"as_of": "yesterday"})
    assert bad.status_code == 400
    anonymous = session_app.test_client()
    query = {"as_of": decided_at.isoformat()}
    assert (
        anonymous.get("/api/car_heater/weather/observations", query_string=query).status_code == 401
    )
    assert anonymous.get("/api/car_heater/weather/health").status_code == 401


def coverage(client, start, end, **query):
    response = client.get(
        "/api/car_heater/weather/coverage",
        query_string={"start": start.isoformat(), "end": end.isoformat(), **query},
    )
    assert response.status_code == 200, response.get_json()
    return {s["source_id"]: s for s in response.get_json()["stations"]}


def test_empty_and_failed_sources_stay_distinct_gaps_and_never_count_as_recovered(
    station, session_app, client
):
    import requests

    build, fmi, clock = station
    failing_start = iso_z(START - timedelta(hours=24))

    def first_window_fails(params):
        if params["fmisid"] == PELMAA and params["starttime"] == failing_start:
            return Response(503, b"busy")
        return None

    fmi.fail_window = first_window_fails
    # Pelmaa answers live; the airport station answers with no series at all.
    fmi.empty.add(AIRPORT)
    collector = build()
    collector.run_pending()

    day = coverage(client, START - timedelta(hours=30), START)
    airport = [(s["kind"], s["start"], s["end"]) for s in day[AIRPORT]["segments"]]
    assert airport == [
        (
            "before_history",
            (START - timedelta(hours=30)).isoformat(),
            day[AIRPORT]["history_start"],
        ),
        ("source_empty", (START - timedelta(hours=24)).isoformat(), START.isoformat()),
    ]
    pelmaa = [s["kind"] for s in day[PELMAA]["segments"]]
    # After a failure the remaining windows wait for a later slot.
    assert pelmaa == ["before_history", "failed", "not_collected", "collected"]
    assert not readings(client, START - timedelta(hours=24), START, fmisid=AIRPORT)

    state = health(client)
    assert state[AIRPORT]["history"]["gap_counts"] == {"source_empty": 1}
    assert state[AIRPORT]["history"]["pending_repair"]["count"] == 0
    assert state[PELMAA]["history"]["gap_counts"] == {"failed": 1, "not_collected": 1}
    assert state[PELMAA]["history"]["pending_repair"]["count"] == 2
    assert state[PELMAA]["history"]["initialized_at"] is None

    # Next slot: the failed window is retried; the empty answer is not re-requested.
    fmi.fail_window = None
    clock[0] += STEP.total_seconds()
    since = len(fmi.requests)
    collector.run_pending()
    assert len(fmi.windows(AIRPORT, since)) == 1
    assert fmi.windows(PELMAA, since)[1][0] == START - timedelta(hours=24)
    state = health(client)
    assert state[PELMAA]["history"]["gap_counts"] == {}
    assert state[PELMAA]["history"]["initialized_at"] == (START + STEP).isoformat()

    # A source that fails live gets no recovery requests in that slot.
    fmi.failing[PELMAA] = requests.ConnectionError("offline")
    clock[0] += STEP.total_seconds()
    since = len(fmi.requests)
    collector.run_pending()
    assert len(fmi.windows(PELMAA, since)) == 1

    anonymous = session_app.test_client()
    assert anonymous.get("/api/car_heater/weather/coverage").status_code == 401
    body = client.get("/api/car_heater/weather/coverage").get_data()
    assert b"FeatureCollection" not in body
    too_long = {"start": "2027-01-01T00:00:00Z", "end": "2027-01-15T00:00:00Z"}
    assert client.get("/api/car_heater/weather/coverage", query_string=too_long).status_code == 400


def test_hung_history_recovery_never_delays_stop_or_changes_departure_timing(
    departure, session_app, client
):
    import threading
    import time

    import requests

    from app.services.weather.observation_archive import ObservationCollector

    _planner, heater, clock, _observation = departure
    release = threading.Event()
    recovering = threading.Event()

    class HungHistoryFMI(StationFMI):
        def get(self, url, params=None, timeout=None):
            if any(r["fmisid"] == params["fmisid"] for r in self.requests):
                # Live already answered; this is a history window.
                self.requests.append(dict(params))
                recovering.set()
                release.wait(10)
                raise requests.Timeout("slow")
            return super().get(url, params, timeout)

    fmi = HungHistoryFMI()
    collector = ObservationCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
    collector.start()
    auth = headers(session_app, client)
    try:
        assert recovering.wait(4), "recovery did not start"
        plan = client.post(
            "/api/car_heater/departure", json={"departure_at": "2027-01-16T08:00"}, headers=auth
        ).get_json()
        # The planner keeps its own observed-temperature source: -10 °C gives 150 minutes.
        assert (plan["preheat_minutes"], plan["fallback_reason"]) == (150, None)
        clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
        heater.observe_bounded_status(
            {
                "bounded": {
                    "protocol": 1,
                    "ready": True,
                    "relay_on": False,
                    "power_w": 0,
                    "boot_id": "relay",
                    "device_boot_id": "esp",
                }
            }
        )
        assert [c["action"] for c in heater.get_queued_commands()] == ["turn_on"]
        started = time.monotonic()
        assert client.delete("/api/car_heater/bounded", headers=auth).status_code == 200
        assert time.monotonic() - started < 1
        assert [c["action"] for c in heater.get_queued_commands()] == ["turn_off"]
    finally:
        release.set()
        collector.stop()
    saved = client.get("/api/car_heater/departure").get_json()
    assert (saved["finish_at"], saved["preheat_minutes"]) == (plan["finish_at"], 150)


def test_a_new_archive_whose_first_attempt_fails_still_initializes_as_new(station, client):
    import requests

    build, fmi, clock = station
    fmi.failing[AIRPORT] = requests.Timeout("slow")
    collector = build()
    collector.run_pending()
    history = health(client)[AIRPORT]["history"]
    assert (history["origin"], history["initialized_at"]) == ("new", None)
    assert history["history_start"] == (START - timedelta(hours=24)).isoformat()

    del fmi.failing[AIRPORT]
    clock[0] += STEP.total_seconds()
    collector.run_pending()
    history = health(client)[AIRPORT]["history"]
    assert history["origin"] == "new"
    assert history["history_start"] == (START - timedelta(hours=24)).isoformat()
    assert history["initialized_at"] == (START + STEP).isoformat()


def test_a_reply_with_values_for_only_part_of_its_window_leaves_the_rest_a_source_gap(
    station, client
):
    build, fmi, _clock = station
    quiet_from, quiet_to = START - timedelta(hours=20), START - timedelta(hours=15)
    for t in grid(quiet_from, quiet_to - STEP):
        # FMI answers these source times, but only with missing markers.
        fmi.overrides[(PELMAA, t)] = "NaN"
    build().run_pending()
    segments = coverage(client, START - timedelta(hours=24), START)[PELMAA]["segments"]
    assert [(s["kind"], s["start"], s["end"]) for s in segments] == [
        ("collected", (START - timedelta(hours=24)).isoformat(), quiet_from.isoformat()),
        ("source_empty", quiet_from.isoformat(), quiet_to.isoformat()),
        ("collected", quiet_to.isoformat(), START.isoformat()),
    ]
    history = health(client)[PELMAA]["history"]
    assert history["gap_counts"] == {"source_empty": 1}
    assert history["pending_repair"]["count"] == 0


def test_live_requests_precede_recovery_and_late_replies_keep_their_real_times(station, client):
    build, fmi, clock = station
    live_start = iso_z(START - timedelta(hours=2))

    def slow_history(params):
        # Each history reply arrives four minutes after it was requested.
        if params["starttime"] != live_start:
            clock[0] += 240

    fmi.on_request = slow_history
    collector = build()
    collector.run_pending()
    first_history = next(i for i, r in enumerate(fmi.requests) if r["starttime"] != live_start)
    # Both stations were asked live before either one's history.
    assert sorted(r["fmisid"] for r in fmi.requests[:first_history]) == [PELMAA, AIRPORT]
    attempts = client.get(
        "/api/car_heater/weather/retrievals",
        query_string={"start": START.isoformat(), "end": (START + timedelta(hours=1)).isoformat()},
    ).get_json()["retrievals"]
    late = [r for r in attempts if r["purpose"] == "initialization"]
    assert late and {r["intended_at"] for r in late} == {START.isoformat()}
    # Replies finishing past later slot boundaries keep their actual receipt times.
    assert max(_parse(r["retrieved_at"]) for r in late) > START + 3 * STEP
    rows = readings(client, START - timedelta(hours=24), START - timedelta(hours=3))
    assert rows and all(_parse(r["retrieved_at"]) > START for r in rows)

    # The next run collects the then-current slot; the overlap leaves no gap.
    fmi.on_request = None
    since = len(fmi.requests)
    assert collector.run_pending() is True
    slot = datetime.fromtimestamp(clock[0] - clock[0] % 600, UTC)
    assert fmi.windows(since=since) == [(slot - timedelta(hours=2), slot)] * 2
    for fmisid in (PELMAA, AIRPORT):
        history = health(client)[fmisid]["history"]
        assert (history["gap_counts"], history["pending_repair"]["count"]) == ({}, 0)


def test_a_failed_progress_write_stays_visible_and_never_blocks_live_collection(
    station, session_app, client, monkeypatch
):
    build, _fmi, _clock = station
    collector = build()
    real = type(session_app.ctrl).ensure_weather_collection_progress

    def broken(ctrl, kind, source_id, **kwargs):
        if source_id == PELMAA:
            raise OSError("disk full")
        return real(ctrl, kind, source_id, **kwargs)

    monkeypatch.setattr(type(session_app.ctrl), "ensure_weather_collection_progress", broken)
    collector.run_pending()
    state = health(client)
    assert state[PELMAA]["archive_error"]["stage"] == "progress"
    assert state[PELMAA]["history"] is None
    assert state[PELMAA]["last_attempt"]["status"] == "ok"
    assert current_times(readings(client, START - timedelta(hours=2), START), PELMAA) == grid(
        START - timedelta(hours=2), START
    )
    assert state[AIRPORT]["history"]["initialized_at"] == START.isoformat()
