"""Continuous station-observation archive through the collector and public APIs.

The FMI HTTP boundary and clock are controlled; parsing, persistence and the
authenticated endpoints are the real ones, on an isolated database.
"""

from datetime import UTC, datetime, timedelta

import pytest
import test_car_heater_sessions as evidence
import test_departure_heating

from app.core import Controller

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
departure = test_departure_heating.departure

PELMAA, AIRPORT = "101486", "137188"
NAMES = {PELMAA: "Seinäjoki Pelmaa", AIRPORT: "Seinäjoki airport"}
START = datetime(2027, 1, 15, 3, 0, tzinfo=UTC)
# These tests pin the live two-hour path; history recovery has its own suite.
LIVE_ONLY = {"repair_requests": 0}


def _stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmi_reply(fmisid, series, *, stamp="2027-01-15T05:00:03Z"):
    """FMI timevaluepair XML in the shape the WFS service returns.

    ``series`` maps parameter -> list of (datetime, value text).
    """
    members = []
    for i, (param, points) in enumerate(series.items(), 1):
        tvps = "".join(
            f"<wml2:point><wml2:MeasurementTVP><wml2:time>{_stamp(t)}</wml2:time>"
            f"<wml2:value>{value}</wml2:value></wml2:MeasurementTVP></wml2:point>"
            for t, value in points
        )
        members.append(f"""
  <wfs:member><omso:PointTimeSeriesObservation gml:id="WFS-{fmisid}-{i}">
    <om:observedProperty xlink:href="https://opendata.fmi.fi/meta?observableProperty=observation&amp;param={param}&amp;language=eng"/>
    <om:featureOfInterest><sams:SF_SpatialSamplingFeature gml:id="fi-1-{i}-{param}">
      <sam:sampledFeature><target:LocationCollection gml:id="sampled-target-1-{i}-{param}">
        <target:member><target:Location gml:id="obsloc-fmisid-{fmisid}-pos-{param}">
          <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">{fmisid}</gml:identifier>
          <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">{NAMES[fmisid]}</gml:name>
          <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/wmo">2833</gml:name>
          <target:region codeSpace="http://xml.fmi.fi/namespace/location/region">Seinäjoki</target:region>
        </target:Location></target:member>
      </target:LocationCollection></sam:sampledFeature>
      <sams:shape><gml:Point gml:id="point-{fmisid}-{i}"><gml:name>{NAMES[fmisid]}</gml:name>
        <gml:pos>62.93808 22.48878 </gml:pos></gml:Point></sams:shape>
    </sams:SF_SpatialSamplingFeature></om:featureOfInterest>
    <om:result><wml2:MeasurementTimeseries gml:id="obs-obs-1-{i}-{param}">{tvps}</wml2:MeasurementTimeseries></om:result>
  </omso:PointTimeSeriesObservation></wfs:member>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection timeStamp="{stamp}" numberMatched="{len(series)}" numberReturned="{len(series)}"
  xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:om="http://www.opengis.net/om/2.0" xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:sam="http://www.opengis.net/sampling/2.0"
  xmlns:sams="http://www.opengis.net/samplingSpatial/2.0" xmlns:wml2="http://www.opengis.net/waterml/2.0"
  xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1">{"".join(members)}
</wfs:FeatureCollection>""".encode()


def every_ten(end, values):
    """Points ending at ``end``, ten minutes apart, oldest first."""
    return [(end - timedelta(minutes=10 * (len(values) - 1 - i)), v) for i, v in enumerate(values)]


class Response:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeFMI:
    """Controlled WFS endpoint: one reply (or exception) per station, recording requests."""

    def __init__(self):
        self.replies = {}
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append({"url": url, "timeout": timeout, **params})
        reply = self.replies[params["fmisid"]]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Response):
            return reply
        return Response(200, reply)


@pytest.fixture
def archive(session_app):
    from app.services.weather.observation_archive import ObservationCollector

    fmi = FakeFMI()
    clock = [START.timestamp()]
    collector = ObservationCollector(
        session_app.ctrl, http=fmi, clock=lambda: clock[0], **LIVE_ONLY
    )
    session_app.weather_collector = collector
    yield collector, fmi, clock


def readings(client, **query):
    query = {"start": "2027-01-14T00:00:00Z", "end": "2027-01-16T00:00:00Z", **query}
    response = client.get("/api/car_heater/weather/observations", query_string=query)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["readings"]


def pick(rows, fmisid, parameter, observed_at):
    return [
        r
        for r in rows
        if r["fmisid"] == fmisid and r["parameter"] == parameter and r["observed_at"] == observed_at
    ]


def test_both_stations_archive_full_series_with_field_times_units_and_missing_values(
    archive, client
):
    collector, fmi, _clock = archive
    end = START
    hour = START.replace(minute=0)
    fmi.replies[PELMAA] = fmi_reply(
        PELMAA,
        {
            "t2m": every_ten(end, ["-6.0", "-6.2", "0.0"]),
            "ri_10min": every_ten(end, ["0.0", "0.4", "NaN"]),
            # Hourly precipitation has values only on the hour at a ten-minute step.
            "r_1h": [(hour - timedelta(minutes=10), "NaN"), (hour, "0.0")],
            "n_man": every_ten(end, ["NaN", "NaN", "NaN"]),
        },
    )
    fmi.replies[AIRPORT] = fmi_reply(
        AIRPORT,
        {
            "t2m": every_ten(end, ["-6.5", "-6.4", "-6.6"]),
            "n_man": every_ten(end, ["8.0", "9.0", "0.0"]),
            "wawa": every_ten(end, ["0.0", "71.0", "NaN"]),
        },
    )
    assert collector.run_pending() is True

    # One aligned two-hour window per station, with every agreed field.
    assert sorted(r["fmisid"] for r in fmi.requests) == [PELMAA, AIRPORT]
    for request in fmi.requests:
        assert request["storedquery_id"] == "fmi::observations::weather::timevaluepair"
        assert request["starttime"] == "2027-01-15T01:00:00Z"
        assert request["endtime"] == "2027-01-15T03:00:00Z"
        assert request["timestep"] == "10"
        assert request["parameters"].split(",") == [
            "t2m",
            "rh",
            "td",
            "ws_10min",
            "wd_10min",
            "wg_10min",
            "n_man",
            "r_1h",
            "ri_10min",
            "wawa",
            "snow_aws",
            "vis",
        ]

    rows = readings(client)
    at = lambda t: t.isoformat()  # noqa: E731
    # Overlapping temperature readings stay separate per station.
    pelmaa_t = pick(rows, PELMAA, "t2m", at(end))
    airport_t = pick(rows, AIRPORT, "t2m", at(end))
    assert [r["value"] for r in pelmaa_t] == [0.0]
    assert [r["value"] for r in airport_t] == [-6.6]
    assert pelmaa_t[0]["status"] == "valid"
    assert pelmaa_t[0]["unit"] == "degC"
    assert pelmaa_t[0]["station_name"] == "Seinäjoki Pelmaa"
    assert pelmaa_t[0]["source_value"] == "0.0"
    # NaN is a received missing value, never zero.
    missing = pick(rows, PELMAA, "ri_10min", at(end))[0]
    assert (missing["status"], missing["value"], missing["source_value"]) == (
        "missing",
        None,
        "NaN",
    )
    assert pick(rows, PELMAA, "ri_10min", at(end - timedelta(minutes=20)))[0]["value"] == 0.0
    # Hourly precipitation keeps its hourly time and interval; no ten-minute values invented.
    hourly = [r for r in rows if r["parameter"] == "r_1h"]
    assert [(r["observed_at"], r["value"], r["period"], r["unit"]) for r in hourly] == [
        (at(hour), 0.0, "PT1H", "mm")
    ]
    # Valid special codes and real zero survive unchanged.
    assert [r["value"] for r in rows if r["fmisid"] == AIRPORT and r["parameter"] == "n_man"] == [
        8.0,
        9.0,
        0.0,
    ]
    assert pick(rows, AIRPORT, "wawa", at(end - timedelta(minutes=10)))[0]["value"] == 71.0
    assert {r["status"] for r in rows if r["fmisid"] == PELMAA and r["parameter"] == "n_man"} == {
        "missing"
    }
    # Every reading links to the retrieval and original reply that delivered it.
    assert all(r["retrieval_id"] and r["reply_id"] and r["retrieved_at"] for r in rows)
    assert all(r["revision"] == 1 and r["current"] for r in rows)
    summaries = {
        r["source_id"]: r["summary"]
        for r in client.get(
            "/api/car_heater/weather/retrievals",
            query_string={"start": "2027-01-14T00:00:00Z", "end": "2027-01-16T00:00:00Z"},
        ).get_json()["retrievals"]
    }
    assert summaries[PELMAA]["skipped_points"] == 1
    assert summaries[PELMAA]["fields_without_values"] == ["n_man"]


def retrievals(client):
    response = client.get(
        "/api/car_heater/weather/retrievals",
        query_string={"start": "2027-01-14T00:00:00Z", "end": "2027-01-16T00:00:00Z"},
    )
    assert response.status_code == 200
    return response.get_json()["retrievals"]


def test_ten_minute_cadence_overlap_corrections_and_restart_keep_history(
    archive, session_app, client
):
    from app.services.weather.observation_archive import ObservationCollector

    collector, fmi, clock = archive
    t0 = START
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(t0, ["-6.0", "NaN"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"t2m": every_ten(t0, ["-7.0", "-7.1"])})
    assert collector.run_pending() is True
    # Within the same ten-minute slot nothing is requested again.
    clock[0] += 9 * 60 + 59
    assert collector.run_pending() is False
    assert len(fmi.requests) == 2

    # Next slot: the window slides by ten minutes and overlaps the previous one.
    clock[0] = (t0 + timedelta(minutes=10, seconds=4)).timestamp()
    t1 = t0 + timedelta(minutes=10)
    # The delayed 03:00 Pelmaa reading arrives; FMI corrects the airport 02:50 value.
    fmi.replies[PELMAA] = fmi_reply(
        PELMAA, {"t2m": every_ten(t1, ["-6.0", "-6.3", "-6.4"])}, stamp="2027-01-15T03:10:05Z"
    )
    fmi.replies[AIRPORT] = fmi_reply(
        AIRPORT, {"t2m": every_ten(t1, ["-7.2", "-7.1", "-7.3"])}, stamp="2027-01-15T03:10:05Z"
    )
    assert collector.run_pending() is True
    second = fmi.requests[2:]
    assert {(r["starttime"], r["endtime"]) for r in second} == {
        ("2027-01-15T01:10:00Z", "2027-01-15T03:10:00Z")
    }

    rows = readings(client)
    late = pick(rows, PELMAA, "t2m", t0.isoformat())
    # What was known earlier (missing) is kept beside the later value.
    assert [(r["revision"], r["status"], r["value"], r["current"]) for r in late] == [
        (1, "missing", None, False),
        (2, "valid", -6.3, True),
    ]
    corrected = pick(rows, AIRPORT, "t2m", (t0 - timedelta(minutes=10)).isoformat())
    assert [(r["revision"], r["value"]) for r in corrected] == [(1, -7.0), (2, -7.2)]
    assert corrected[0]["retrieved_at"] < corrected[1]["retrieved_at"]
    # Unchanged overlapping values are not duplicated.
    assert len(pick(rows, AIRPORT, "t2m", t0.isoformat())) == 1
    before = len(rows)

    # Restart: a new Controller and collector re-collect the same content.
    session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
    session_app.ctrl._sa_engine = session_app.extensions["session_engine"]
    restarted = ObservationCollector(
        session_app.ctrl, http=fmi, clock=lambda: clock[0], **LIVE_ONLY
    )
    session_app.weather_collector = restarted
    assert restarted.run_pending() is True
    assert len(readings(client)) == before
    history = retrievals(client)
    assert len(history) == 6
    assert all(r["status"] == "ok" for r in history)
    # Identical replies are stored once but every retrieval keeps its own time.
    last_two = [r for r in history if r["source_id"] == PELMAA][-2:]
    assert last_two[0]["reply_id"] == last_two[1]["reply_id"]
    assert last_two[0]["id"] != last_two[1]["id"]
    assert last_two[1]["summary"]["new"] == 0
    assert last_two[1]["summary"]["unchanged"] == 3


def health(client):
    response = client.get("/api/car_heater/weather/health")
    assert response.status_code == 200
    return {s["source_id"]: s for s in response.get_json()["sources"]}


def test_failed_station_never_discards_the_other_and_health_tells_them_apart(archive, client):
    import requests

    collector, fmi, clock = archive
    assert set(health(client)) == {PELMAA, AIRPORT}
    assert all(s["last_attempt"] is None and s["stale"] for s in health(client).values())

    fmi.replies[PELMAA] = requests.Timeout("slow")
    # A successful reply whose fields are all missing is not a collector failure.
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"t2m": every_ten(START, ["NaN", "NaN"])})
    collector.run_pending()
    state = health(client)
    assert state[PELMAA]["last_failure"]["error"] == "timeout"
    assert state[PELMAA]["last_success"] is None
    assert state[PELMAA]["consecutive_failures"] == 1
    assert state[AIRPORT]["last_failure"] is None
    summary = state[AIRPORT]["last_success"]["summary"]
    assert summary["fields_with_values"] == []
    assert summary["fields_without_values"] == ["t2m"]
    assert "vis" in summary["fields_absent"]
    assert state[AIRPORT]["station_name"] == "Seinäjoki airport"
    assert not state[AIRPORT]["stale"]

    for broken, error in [
        (b"<html>Service unavailable</html>", "malformed_reply"),
        (b"<wfs:FeatureCollection", "malformed_reply"),
        (Response(503, b"busy"), "http_error"),
        (requests.ConnectionError("offline"), "request_failed:ConnectionError"),
    ]:
        clock[0] += 600
        fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-5.0"])})
        fmi.replies[AIRPORT] = broken
        collector.run_pending()
        state = health(client)
        assert state[AIRPORT]["last_attempt"]["error"] == error
        assert state[AIRPORT]["last_attempt"]["reply_id"] is None
        assert state[PELMAA]["last_attempt"]["status"] == "ok"
    assert state[AIRPORT]["consecutive_failures"] == 4
    assert state[PELMAA]["consecutive_failures"] == 0
    assert pick(readings(client), PELMAA, "t2m", START.isoformat())[0]["value"] == -5.0

    # An empty collection is a successful reply with nothing in it.
    clock[0] += 600
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {})
    collector.run_pending()
    state = health(client)
    assert state[AIRPORT]["last_attempt"]["status"] == "ok"
    assert state[AIRPORT]["last_attempt"]["summary"]["series"] == 0
    assert state[AIRPORT]["consecutive_failures"] == 0

    # A collector that stops attempting becomes visibly stale.
    clock[0] += 26 * 60
    assert all(s["stale"] for s in health(client).values())


def test_archive_write_failure_is_visible_and_isolated_per_station(archive, client, monkeypatch):
    collector, fmi, clock = archive
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-5.0"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"t2m": every_ten(START, ["-6.0"])})
    real = type(collector._ctrl).record_weather_observation_retrieval

    def flaky(ctrl, retrieval, *args, **kwargs):
        if retrieval["source_id"] == PELMAA:
            raise OSError("disk full")
        return real(ctrl, retrieval, *args, **kwargs)

    monkeypatch.setattr(type(collector._ctrl), "record_weather_observation_retrieval", flaky)
    collector.run_pending()
    state = health(client)
    assert state[PELMAA]["archive_error"]["error"] == "OSError"
    assert state[AIRPORT]["last_attempt"]["status"] == "ok"
    monkeypatch.setattr(type(collector._ctrl), "record_weather_observation_retrieval", real)
    clock[0] += 600
    collector.run_pending()
    assert "archive_error" not in health(client)[PELMAA]
    assert pick(readings(client), PELMAA, "t2m", START.isoformat())[0]["value"] == -5.0


def test_original_replies_are_explicit_authenticated_and_not_embedded(archive, session_app, client):
    collector, fmi, _clock = archive
    original = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-5.0"])})
    fmi.replies[PELMAA] = original
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"wawa": every_ten(START, ["0.0"])})
    collector.run_pending()
    rows = readings(client, fmisid=PELMAA)
    assert {r["fmisid"] for r in rows} == {PELMAA}
    reply_id = rows[0]["reply_id"]
    for path in (
        "/api/car_heater/weather/observations",
        "/api/car_heater/weather/retrievals",
        "/api/car_heater/weather/health",
    ):
        body = client.get(path).get_data()
        assert b"FeatureCollection" not in body
    reply = client.get(f"/api/car_heater/weather/replies/{reply_id}")
    assert reply.status_code == 200
    assert reply.data == original
    assert reply.mimetype == "application/xml"
    assert reply.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in reply.headers["Content-Disposition"]
    assert client.get("/api/car_heater/weather/replies/999").status_code == 404
    anonymous = session_app.test_client()
    for path in (
        f"/api/car_heater/weather/replies/{reply_id}",
        "/api/car_heater/weather/observations",
        "/api/car_heater/weather/retrievals",
        "/api/car_heater/weather/health",
    ):
        assert anonymous.get(path).status_code == 401
    too_long = {"start": "2027-01-01T00:00:00Z", "end": "2027-01-15T00:00:00Z"}
    assert (
        client.get("/api/car_heater/weather/observations", query_string=too_long).status_code == 400
    )
    assert (
        client.get(
            "/api/car_heater/weather/observations", query_string={"start": "yesterday"}
        ).status_code
        == 400
    )


def test_sqlite_backup_restore_keeps_readings_replies_and_provenance(
    archive, session_app, client, tmp_path
):
    import json
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine

    collector, fmi, clock = archive
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-6.0", "NaN"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"n_man": every_ten(START, ["8.0"])})
    collector.run_pending()
    clock[0] += 600
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-6.1", "-6.2"])})
    collector.run_pending()
    before_rows, before_history = readings(client), retrievals(client)
    originals = {
        r["reply_id"]: client.get(f"/api/car_heater/weather/replies/{r['reply_id']}").data
        for r in before_history
    }

    source = tmp_path / "sessions.db"
    backup, restored = tmp_path / "backup.db", tmp_path / "restored.db"
    for frm, to in [(source, backup), (backup, restored)]:
        subprocess.run(
            [sys.executable, "scripts/heater_history.py", "sqlite-copy", str(frm), str(to)],
            check=True,
            capture_output=True,
        )
    report = json.loads(
        subprocess.run(
            [sys.executable, "scripts/heater_history.py", "discover"],
            env={**os.environ, "DATABASE_URL": f"sqlite:///{restored}"},
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert report["tables"]["weather_observation"]["count"] == len(before_rows)
    assert report["tables"]["weather_retrieval"]["count"] == 4
    integrity = report["weather_integrity"]
    # Collection checkpoints are listed; their own checks are in the evidence restore suite.
    assert {p["source_id"] for p in integrity.pop("collection_progress")} == {PELMAA, AIRPORT}
    assert integrity == {
        "replies_checked": 3,
        "mismatched_replies": [],
        "retrievals_without_reply": [],
        "successful_retrievals_without_reply": [],
        "readings_without_retrieval": [],
        "progress_without_retrievals": [],
        "forecast_links": {
            "values_without_content": [],
            "versions_without_content": [],
            "versions_without_first_retrieval": [],
            "contents_without_first_retrieval": [],
            "receipts_without_retrieval": [],
            "receipts_without_version": [],
        },
    }

    engine = create_engine(f"sqlite:///{restored}")
    try:
        session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
        session_app.ctrl._sa_engine = engine
        assert readings(client) == before_rows
        assert retrievals(client) == before_history
        for reply_id, content in originals.items():
            assert client.get(f"/api/car_heater/weather/replies/{reply_id}").data == content
        # The restored archive keeps collecting.
        from app.services.weather.observation_archive import ObservationCollector

        clock[0] += 600
        resumed = ObservationCollector(
            session_app.ctrl, http=fmi, clock=lambda: clock[0], **LIVE_ONLY
        )
        resumed.run_pending()
        assert len(retrievals(client)) == 6
    finally:
        engine.dispose()


def test_discovery_reports_damaged_replies_and_broken_provenance(archive, session_app, tmp_path):
    from scripts.heater_history import discover
    from sqlalchemy import text

    collector, fmi, _clock = archive
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-6.0"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"t2m": every_ten(START, ["-7.0"])})
    collector.run_pending()
    with session_app.extensions["session_engine"].begin() as conn:
        first = conn.execute(text("SELECT MIN(id) FROM weather_reply")).scalar()
        conn.execute(
            text("UPDATE weather_reply SET content = :c WHERE id = :i"),
            {"c": __import__("gzip").compress(b"tampered"), "i": first},
        )
        conn.execute(
            text("UPDATE weather_retrieval SET reply_id = 999 WHERE reply_id != :i"), {"i": first}
        )
        conn.execute(
            text("UPDATE weather_observation SET retrieval_id = 998 WHERE fmisid = :f"),
            {"f": PELMAA},
        )
    report = discover(f"sqlite:///{tmp_path / 'sessions.db'}")
    integrity = report["weather_integrity"]
    assert integrity["replies_checked"] == 2
    assert integrity["mismatched_replies"] == [first]
    assert len(integrity["retrievals_without_reply"]) == 1
    assert len(integrity["readings_without_retrieval"]) == 1


def test_collector_thread_runs_without_a_browser_or_telemetry(session_app):
    import time

    from app.services.weather.observation_archive import ObservationCollector

    fmi = FakeFMI()
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-6.0"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"t2m": every_ten(START, ["-7.0"])})
    collector = ObservationCollector(
        session_app.ctrl, http=fmi, clock=lambda: START.timestamp(), **LIVE_ONLY
    )
    collector.start()
    try:
        deadline = time.monotonic() + 4
        while len(fmi.requests) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert collector.running
        assert sorted(r["fmisid"] for r in fmi.requests) == [PELMAA, AIRPORT]
    finally:
        collector.stop()
    assert not collector.running


def test_hung_or_failing_collection_never_delays_stop_or_changes_departure_timing(
    departure, session_app, client
):
    import threading
    import time

    from app.services.weather.observation_archive import ObservationCollector

    _planner, heater, clock, _observation = departure
    release = threading.Event()

    class HungFMI(FakeFMI):
        def get(self, url, params=None, timeout=None):
            self.requests.append(params)
            if params["fmisid"] == PELMAA:
                release.wait(10)
                raise __import__("requests").Timeout("slow")
            return Response(200, b"<not-xml")

    fmi = HungFMI()
    collector = ObservationCollector(
        session_app.ctrl, http=fmi, clock=lambda: clock[0], **LIVE_ONLY
    )
    collector.start()
    auth = headers(session_app, client)
    try:
        deadline = time.monotonic() + 4
        while not fmi.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        assert fmi.requests, "collector did not start"
        # The planner keeps its own observed-temperature source: -10 °C gives 150 minutes.
        plan = client.post(
            "/api/car_heater/departure", json={"departure_at": "2027-01-16T08:00"}, headers=auth
        ).get_json()
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
        on = heater.get_queued_commands()
        assert [c["action"] for c in on] == ["turn_on"]
        assert on[0]["finish_at"] == datetime.fromisoformat(plan["finish_at"]).timestamp()
        started = time.monotonic()
        assert client.delete("/api/car_heater/bounded", headers=auth).status_code == 200
        assert time.monotonic() - started < 1
        assert [c["action"] for c in heater.get_queued_commands()] == ["turn_off"]
    finally:
        release.set()
        collector.stop()
    saved = client.get("/api/car_heater/departure").get_json()
    assert saved["finish_at"] == plan["finish_at"]
    assert saved["preheat_minutes"] == 150
