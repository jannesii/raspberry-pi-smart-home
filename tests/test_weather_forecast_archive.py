"""Parking-point forecast archive through the collector and public APIs.

The FMI HTTP boundary and clock are controlled; parsing, persistence and the
authenticated endpoints are the real ones, on an isolated database. Reply XML
follows the shape of a live HARMONIE point reply sampled on 2026-10-16.
"""

from datetime import UTC, datetime, timedelta

import pytest
import test_car_heater_sessions as evidence
import test_departure_heating
from test_weather_observation_archive import FakeFMI, Response

from app.core import Controller

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
departure = test_departure_heating.departure

POINT = "62.8000,22.8260"
START = datetime(2027, 1, 15, 3, 0, tzinfo=UTC)
ORIGIN = datetime(2027, 1, 15, 0, 0, tzinfo=UTC)
FIELDS = (
    "Temperature",
    "DewPoint",
    "Humidity",
    "WindSpeedMS",
    "WindDirection",
    "WindGust",
    "TotalCloudCover",
    "RadiationLW",
    "RadiationGlobal",
    "Precipitation1h",
    "WeatherSymbol3",
)
PROCEDURE = "http://xml.fmi.fi/inspire/process/harmonie_scandinavia_surface"


def _stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def forecast_reply(
    series,
    *,
    origin=ORIGIN,
    result=None,
    pos="62.80000 22.82600",
    procedure=PROCEDURE,
    stamp="2027-01-15T03:00:04Z",
):
    """FMI HARMONIE point timevaluepair XML.

    ``series`` maps parameter -> list of (valid datetime, value text).
    """
    result = result or origin + timedelta(hours=3, minutes=5, seconds=41)
    members = []
    for i, (param, points) in enumerate(series.items(), 1):
        times = [t for t, _ in points] or [origin]
        tvps = "".join(
            f"<wml2:point><wml2:MeasurementTVP><wml2:time>{_stamp(t)}</wml2:time>"
            f"<wml2:value>{value}</wml2:value></wml2:MeasurementTVP></wml2:point>"
            for t, value in points
        )
        if i == 1:
            span, stamps = min(times), max(times)
            times_xml = f"""
    <om:phenomenonTime><gml:TimePeriod gml:id="time-interval-1-1">
      <gml:beginPosition>{_stamp(span)}</gml:beginPosition>
      <gml:endPosition>{_stamp(stamps)}</gml:endPosition>
    </gml:TimePeriod></om:phenomenonTime>
    <om:resultTime><gml:TimeInstant gml:id="time-1-1">
      <gml:timePosition>{_stamp(result)}</gml:timePosition>
    </gml:TimeInstant></om:resultTime>"""
        else:
            # Like live FMI replies, later members refer to the first member's times.
            times_xml = """
    <om:phenomenonTime xlink:href="#time-interval-1-1"/>
    <om:resultTime xlink:href="#time-1-1"/>"""
        members.append(f"""
  <wfs:member><omso:PointTimeSeriesObservation gml:id="WFS-{i}--{param}">{times_xml}
    <om:procedure xlink:href="{procedure}"/>
    <om:parameter><om:NamedValue>
      <om:name xlink:href="https://inspire.ec.europa.eu/codeList/ProcessParameterValue/value/numericalModel/analysisTime"/>
      <om:value><gml:TimeInstant gml:id="analysis-time-1-{i}-{param}--">
        <gml:timePosition>{_stamp(origin)}</gml:timePosition>
      </gml:TimeInstant></om:value>
    </om:NamedValue></om:parameter>
    <om:observedProperty xlink:href="https://opendata.fmi.fi/meta?observableProperty=forecast&amp;param={param}&amp;language=eng"/>
    <om:featureOfInterest><sams:SF_SpatialSamplingFeature gml:id="enn-s-1-{i}-{param}">
      <sam:sampledFeature><target:LocationCollection gml:id="sampled-target-1-{i}-{param}">
        <target:member><target:Location gml:id="forloc-geoid-637219-pos-{param}">
          <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/geoid">637219</gml:identifier>
          <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Seinäjoki</gml:name>
          <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/geoid">637219</gml:name>
          <target:country codeSpace="http://xml.fmi.fi/namespace/location/country">Finland</target:country>
          <target:timezone>Europe/Helsinki</target:timezone>
          <target:region codeSpace="http://xml.fmi.fi/namespace/location/region">Finland</target:region>
        </target:Location></target:member>
      </target:LocationCollection></sam:sampledFeature>
      <sams:shape><gml:MultiPoint gml:id="foi-multipoint-1-{i}-{param}"><gml:pointMembers>
        <gml:Point gml:id="point-637219-{param}" srsName="http://www.opengis.net/def/crs/EPSG/0/4326" srsDimension="2">
          <gml:name>Seinäjoki</gml:name><gml:pos>{pos} </gml:pos>
        </gml:Point></gml:pointMembers></gml:MultiPoint></sams:shape>
    </sams:SF_SpatialSamplingFeature></om:featureOfInterest>
    <om:result><wml2:MeasurementTimeseries gml:id="mts-1-{i}-{param}">{tvps}</wml2:MeasurementTimeseries></om:result>
  </omso:PointTimeSeriesObservation></wfs:member>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection timeStamp="{stamp}" numberMatched="{len(series)}" numberReturned="{len(series)}"
  xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:om="http://www.opengis.net/om/2.0" xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:sam="http://www.opengis.net/sampling/2.0"
  xmlns:sams="http://www.opengis.net/samplingSpatial/2.0" xmlns:wml2="http://www.opengis.net/waterml/2.0"
  xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1">{"".join(members)}
</wfs:FeatureCollection>""".encode()


def hourly(first, values):
    """Points one hour apart starting at ``first``."""
    return [(first + timedelta(hours=i), v) for i, v in enumerate(values)]


def full_run(hours, *, origin=ORIGIN, offset=0.0, **kwargs):
    """Every agreed field for ``hours`` hourly steps after the origin."""
    first = origin + timedelta(hours=1)
    series = {}
    for n, param in enumerate(FIELDS):
        series[param] = hourly(first, [f"{n + offset + h / 10:.1f}" for h in range(hours)])
    return forecast_reply(series, origin=origin, **kwargs)


class FakeForecastFMI(FakeFMI):
    """One queued reply (or exception) per request, recording requests."""

    def __init__(self):
        super().__init__()
        self.reply = None

    def get(self, url, params=None, timeout=None):
        self.requests.append({"url": url, "timeout": timeout, **params})
        reply = self.reply
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Response):
            return reply
        return Response(200, reply)


@pytest.fixture
def forecasts(session_app):
    from app.services.weather.forecast_archive import ForecastCollector

    fmi = FakeForecastFMI()
    clock = [START.timestamp()]
    collector = ForecastCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
    session_app.weather_forecast_collector = collector
    yield collector, fmi, clock


RANGE = {"start": "2027-01-14T00:00:00Z", "end": "2027-01-16T00:00:00Z"}


def versions(client, **query):
    response = client.get("/api/car_heater/weather/forecasts", query_string={**RANGE, **query})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def detail(client, version_id):
    response = client.get(f"/api/car_heater/weather/forecasts/{version_id}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_thirty_minute_check_archives_the_full_returned_horizon_with_source_times(
    forecasts, client
):
    collector, fmi, _clock = forecasts
    # A horizon length the collector never asks for: whatever FMI returns is kept.
    fmi.reply = full_run(53)
    assert collector.run_pending() is True

    [request] = fmi.requests
    assert request["storedquery_id"] == "fmi::forecast::harmonie::surface::point::timevaluepair"
    assert request["latlon"] == POINT
    assert request["parameters"].split(",") == list(FIELDS)
    assert request["timestep"] == "60"
    # The response horizon is the source default, not one hardcoded here.
    assert "endtime" not in request and "forecasthours" not in request

    listing = versions(client)
    [version] = listing["versions"]
    assert version["source_id"] == POINT
    assert version["requested_location"] == {"lat": 62.8, "lon": 22.826}
    assert version["location"]["geoid"] == "637219"
    assert version["location"]["name"] == "Seinäjoki"
    assert version["location"]["pos"] == "62.80000 22.82600"
    assert version["procedure"] == PROCEDURE
    assert version["analysis_time"] == "2027-01-15T00:00:00+00:00"
    assert version["result_time"] == "2027-01-15T03:05:41+00:00"
    assert version["valid_start"] == "2027-01-15T01:00:00+00:00"
    assert version["valid_end"] == "2027-01-17T05:00:00+00:00"
    assert version["value_count"] == 53 * len(FIELDS)
    assert version["revision"] == 1 and version["current"] is True
    [receipt] = version["receipts"]
    assert receipt["retrieved_at"] == START.isoformat()
    assert receipt["reply_id"]
    assert "values" not in version

    full = detail(client, version["id"])
    values = full["values"]
    assert len(values) == 53 * len(FIELDS)
    by_field = {}
    for value in values:
        by_field.setdefault(value["parameter"], []).append(value)
    assert sorted(by_field) == sorted(FIELDS)
    assert all(len(rows) == 53 for rows in by_field.values())
    lw = by_field["RadiationLW"][0]
    # Long-wave radiation keeps its source identity and unit, with no direction inferred.
    assert (lw["valid_at"], lw["status"], lw["value"], lw["source_value"], lw["unit"]) == (
        "2027-01-15T01:00:00+00:00",
        "valid",
        7.0,
        "7.0",
        "W/m2",
    )
    assert lw["property_href"].endswith("param=RadiationLW&language=eng")
    assert "direction" not in str(lw).lower()
    rain = by_field["Precipitation1h"][0]
    assert (rain["unit"], rain["period"], rain["statistic"]) == ("mm/h", "PT1H", "sum")
    assert by_field["TotalCloudCover"][0]["unit"] == "%"
    assert by_field["WeatherSymbol3"][0]["unit"] == "index"
    assert by_field["Temperature"][-1]["valid_at"] == "2027-01-17T05:00:00+00:00"
    assert full["receipts"] == version["receipts"]


def retrievals(client):
    response = client.get(
        "/api/car_heater/weather/retrievals", query_string={**RANGE, "kind": "forecast"}
    )
    assert response.status_code == 200
    return response.get_json()["retrievals"]


def test_versions_are_kept_as_received_across_repeats_revisions_order_and_restart(
    forecasts, session_app, client
):
    from app.services.weather.forecast_archive import ForecastCollector

    collector, fmi, clock = forecasts
    run_00 = full_run(4, stamp="2027-01-15T03:00:04Z")
    fmi.reply = run_00
    assert collector.run_pending() is True
    # Within the same thirty-minute slot nothing is requested again.
    clock[0] += 29 * 60 + 59
    assert collector.run_pending() is False
    assert len(fmi.requests) == 1

    # Next check: the same run again; only the reply stamp differs.
    clock[0] = (START + timedelta(minutes=30, seconds=3)).timestamp()
    fmi.reply = full_run(4, stamp="2027-01-15T03:30:05Z")
    assert collector.run_pending() is True
    [only] = versions(client)["versions"]
    assert [r["retrieved_at"] for r in only["receipts"]] == [
        START.isoformat(),
        (START + timedelta(minutes=30, seconds=3)).isoformat(),
    ]
    first_reply, second_reply = (r["reply_id"] for r in only["receipts"])
    assert first_reply != second_reply
    assert client.get(f"/api/car_heater/weather/replies/{first_reply}").data == run_00

    # FMI republishes the same origin and result time with a changed value.
    clock[0] += 1800
    changed = forecast_reply(
        {
            **{p: hourly(ORIGIN + timedelta(hours=1), ["0.0"]) for p in FIELDS},
            "Temperature": hourly(ORIGIN + timedelta(hours=1), ["-9.5"]),
        }
    )
    fmi.reply = changed
    collector.run_pending()
    # Same values as revision 2 but a later result time: a further revision, values shared.
    clock[0] += 1800
    fmi.reply = forecast_reply(
        {
            **{p: hourly(ORIGIN + timedelta(hours=1), ["0.0"]) for p in FIELDS},
            "Temperature": hourly(ORIGIN + timedelta(hours=1), ["-9.5"]),
        },
        result=ORIGIN + timedelta(hours=4),
    )
    collector.run_pending()
    # A newer run, then an older run served out of order.
    clock[0] += 1800
    fmi.reply = full_run(3, origin=ORIGIN + timedelta(hours=3), offset=1)
    collector.run_pending()
    clock[0] += 1800
    fmi.reply = full_run(3, origin=ORIGIN - timedelta(hours=3), offset=2)
    collector.run_pending()

    listing = versions(client)["versions"]
    summary = [(v["analysis_time"][11:16], v["revision"], v["current"]) for v in listing]
    assert summary == [
        ("00:00", 1, False),
        ("00:00", 2, False),
        ("00:00", 3, True),
        ("03:00", 1, True),
        ("21:00", 1, True),
    ]
    rev1, rev2, rev3 = listing[:3]
    assert rev1["result_time"] == rev2["result_time"] != rev3["result_time"]
    # The earlier values stay retrievable beside the revision.
    temp = lambda v: [  # noqa: E731
        x["source_value"]
        for x in detail(client, v["id"])["values"]
        if x["parameter"] == "Temperature"
    ]
    assert temp(rev1) == ["0.0", "0.1", "0.2", "0.3"]
    assert temp(rev2) == temp(rev3) == ["-9.5"]
    # Each receipt keeps its own retrieval; the old run arrived last.
    assert listing[-1]["first_retrieved_at"] > listing[-2]["first_retrieved_at"]
    before = len(listing)
    history = retrievals(client)
    assert len(history) == 6
    assert history[-1]["summary"]["versions"][0]["new"] is True

    # Restart: a new Controller and collector receive the current run again.
    session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
    session_app.ctrl._sa_engine = session_app.extensions["session_engine"]
    clock[0] += 1800
    restarted = ForecastCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
    session_app.weather_forecast_collector = restarted
    fmi.reply = full_run(3, origin=ORIGIN + timedelta(hours=3), offset=1, stamp="later")
    assert restarted.run_pending() is True
    after = versions(client)["versions"]
    assert len(after) == before
    newest = next(v for v in after if v["analysis_time"].startswith("2027-01-15T03:00"))
    assert len(newest["receipts"]) == 2
    last = retrievals(client)[-1]["summary"]
    assert (last["new_versions"], last["repeated_versions"]) == (0, 1)


def forecast_health(client):
    response = client.get("/api/car_heater/weather/health")
    assert response.status_code == 200
    return response.get_json()["forecast"]


def test_failures_missing_fields_and_unobserved_periods_are_told_apart(forecasts, client):
    import requests

    collector, fmi, clock = forecasts
    state = forecast_health(client)
    assert state["collector"]["interval_s"] == 1800
    [source] = state["sources"]
    assert source["source_id"] == POINT and source["last_attempt"] is None and source["stale"]

    # A successful reply with an all-NaN field and a field not returned at all.
    series = {p: hourly(ORIGIN + timedelta(hours=1), ["1.0", "0.0"]) for p in FIELDS}
    series["RadiationLW"] = hourly(ORIGIN + timedelta(hours=1), ["NaN", "NaN"])
    series["WeatherSymbol3"] = hourly(ORIGIN + timedelta(hours=1), ["NaN", "-1.5e"])
    del series["WindGust"]
    fmi.reply = forecast_reply(series)
    collector.run_pending()
    [version] = versions(client)["versions"]
    assert version["fields_without_values"] == ["RadiationLW", "WeatherSymbol3"]
    assert version["fields_absent"] == ["WindGust"]
    values = detail(client, version["id"])["values"]
    lw = [v for v in values if v["parameter"] == "RadiationLW"]
    assert [(v["status"], v["value"], v["source_value"]) for v in lw] == [
        ("missing", None, "NaN")
    ] * 2
    symbol = [v["status"] for v in values if v["parameter"] == "WeatherSymbol3"]
    assert symbol == ["missing", "invalid"]
    # True zero survives.
    assert [v["value"] for v in values if v["parameter"] == "RadiationGlobal"] == [1.0, 0.0]
    [source] = forecast_health(client)["sources"]
    assert source["last_failure"] is None and not source["stale"]
    assert source["last_success"]["summary"]["fields_absent"] == ["WindGust"]
    assert source["last_success"]["summary"]["versions"][0]["analysis_time"] == ORIGIN.isoformat()

    # 03:30 times out; the server is down at 04:00; 04:30 is malformed; 05:00 succeeds.
    clock[0] += 1800
    fmi.reply = requests.Timeout("slow")
    collector.run_pending()
    [source] = forecast_health(client)["sources"]
    assert source["last_attempt"]["error"] == "timeout"
    assert source["consecutive_failures"] == 1
    clock[0] += 3600
    fmi.reply = b"<html>busy</html>"
    collector.run_pending()
    clock[0] += 1800
    fmi.reply = forecast_reply({})
    collector.run_pending()
    [source] = forecast_health(client)["sources"]
    assert source["consecutive_failures"] == 0
    # An empty collection is a received reply holding no version.
    assert source["last_success"]["summary"]["versions"] == []
    assert len(versions(client)["versions"]) == 1
    history = retrievals(client)
    assert [r["status"] for r in history] == ["ok", "failed", "failed", "ok"]
    assert [r["reply_id"] is None for r in history] == [False, True, True, False]
    assert history[1]["requested_start"] is None and history[1]["requested_end"] is None

    # Coverage names every stretch without a successful check, never a captured run.
    clock[0] += 2 * 3600
    listing = versions(client, start="2027-01-15T02:00:00Z", end="2027-01-15T09:00:00Z")
    assert listing["unobserved"] == [
        {
            "start": "2027-01-15T02:00:00+00:00",
            "end": "2027-01-15T03:00:00+00:00",
            "failed_attempts": 0,
        },
        {
            "start": "2027-01-15T03:00:00+00:00",
            "end": "2027-01-15T05:00:00+00:00",
            "failed_attempts": 2,
        },
        {
            "start": "2027-01-15T05:00:00+00:00",
            "end": "2027-01-15T07:00:00+00:00",
            "failed_attempts": 0,
        },
    ]
    assert listing["interval_s"] == 1800
    # No check for over two cadences: the readout says so, with the day's gaps.
    [source] = forecast_health(client)["sources"]
    assert source["stale"]
    assert source["unobserved_24h"]["count"] == 3
    assert source["unobserved_24h"]["latest"] == {
        "start": "2027-01-15T05:00:00+00:00",
        "end": "2027-01-15T07:00:00+00:00",
        "failed_attempts": 0,
    }


def test_forecast_evidence_is_authenticated_and_replies_are_explicit(
    forecasts, session_app, client
):
    collector, fmi, _clock = forecasts
    original = full_run(2)
    fmi.reply = original
    collector.run_pending()
    [version] = versions(client)["versions"]
    reply_id = version["receipts"][0]["reply_id"]
    for path in (
        "/api/car_heater/weather/forecasts",
        f"/api/car_heater/weather/forecasts/{version['id']}",
        "/api/car_heater/weather/retrievals?kind=forecast",
        "/api/car_heater/weather/health",
    ):
        body = client.get(path).get_data()
        assert b"FeatureCollection" not in body and b"MeasurementTVP" not in body
    reply = client.get(f"/api/car_heater/weather/replies/{reply_id}")
    assert reply.status_code == 200 and reply.data == original
    assert "attachment" in reply.headers["Content-Disposition"]
    assert client.get("/api/car_heater/weather/forecasts/999").status_code == 404
    assert client.get("/api/car_heater/weather/retrievals?kind=everything").status_code == 400
    anonymous = session_app.test_client()
    for path in (
        "/api/car_heater/weather/forecasts",
        f"/api/car_heater/weather/forecasts/{version['id']}",
        f"/api/car_heater/weather/replies/{reply_id}",
    ):
        assert anonymous.get(path).status_code == 401


def test_archive_write_failure_is_visible_and_retried(forecasts, client, monkeypatch):
    collector, fmi, clock = forecasts
    fmi.reply = full_run(2)
    real = type(collector._ctrl).record_weather_forecast_retrieval

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(type(collector._ctrl), "record_weather_forecast_retrieval", broken)
    collector.run_pending()
    [source] = forecast_health(client)["sources"]
    assert source["archive_error"]["error"] == "OSError"
    monkeypatch.setattr(type(collector._ctrl), "record_weather_forecast_retrieval", real)
    clock[0] += 1800
    collector.run_pending()
    [source] = forecast_health(client)["sources"]
    assert "archive_error" not in source
    assert len(versions(client)["versions"]) == 1


def test_collector_thread_runs_on_its_own(session_app):
    import time

    from app.services.weather.forecast_archive import ForecastCollector

    fmi = FakeForecastFMI()
    fmi.reply = full_run(2)
    collector = ForecastCollector(session_app.ctrl, http=fmi, clock=lambda: START.timestamp())
    collector.start()
    try:
        deadline = time.monotonic() + 4
        while not fmi.requests and time.monotonic() < deadline:
            time.sleep(0.05)
        assert collector.running and len(fmi.requests) == 1
    finally:
        collector.stop()
    assert not collector.running


def test_hung_or_failing_forecast_never_delays_stop_or_changes_departure_timing(
    departure, session_app, client
):
    import threading
    import time

    import requests

    from app.services.weather.forecast_archive import ForecastCollector

    _planner, heater, clock, _observation = departure
    release = threading.Event()

    class HungFMI(FakeForecastFMI):
        def get(self, url, params=None, timeout=None):
            self.requests.append(params)
            release.wait(10)
            raise requests.Timeout("slow")

    fmi = HungFMI()
    collector = ForecastCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
    collector.start()
    auth = headers(session_app, client)
    try:
        deadline = time.monotonic() + 4
        while not fmi.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        assert fmi.requests, "collector did not start"
        # The planner keeps its observed-temperature source: -10 °C gives 150 minutes.
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


def test_sqlite_backup_restore_keeps_versions_values_replies_and_receipts(
    forecasts, session_app, client, tmp_path
):
    import json
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine

    collector, fmi, clock = forecasts
    fmi.reply = full_run(3)
    collector.run_pending()
    clock[0] += 1800
    fmi.reply = full_run(3, stamp="again")
    collector.run_pending()
    clock[0] += 1800
    fmi.reply = full_run(3, origin=ORIGIN + timedelta(hours=3))
    collector.run_pending()
    before = versions(client)["versions"]
    details = {v["id"]: detail(client, v["id"]) for v in before}
    replies = {
        r["reply_id"]: client.get(f"/api/car_heater/weather/replies/{r['reply_id']}").data
        for v in before
        for r in v["receipts"]
    }
    assert len(replies) == 3

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
    tables = report["tables"]
    assert tables["weather_forecast_version"]["count"] == 2
    assert tables["weather_forecast_receipt"]["count"] == 3
    assert tables["weather_forecast_value"]["count"] == 2 * 3 * len(FIELDS)
    assert tables["weather_forecast_value"]["newest"] == "2027-01-15T06:00:00+00:00"
    integrity = report["weather_integrity"]
    assert integrity["replies_checked"] == 3 and integrity["mismatched_replies"] == []
    assert integrity["forecast_links"] == {
        "values_without_content": [],
        "versions_without_content": [],
        "versions_without_first_retrieval": [],
        "contents_without_first_retrieval": [],
        "receipts_without_retrieval": [],
        "receipts_without_version": [],
    }

    engine = create_engine(f"sqlite:///{restored}")
    try:
        session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
        session_app.ctrl._sa_engine = engine
        assert versions(client)["versions"] == before
        assert {v["id"]: detail(client, v["id"]) for v in before} == details
        for reply_id, content in replies.items():
            assert client.get(f"/api/car_heater/weather/replies/{reply_id}").data == content
    finally:
        engine.dispose()


def test_discovery_reports_broken_forecast_links(forecasts, session_app, tmp_path):
    from scripts.heater_history import discover
    from sqlalchemy import text

    collector, fmi, _clock = forecasts
    fmi.reply = full_run(2)
    collector.run_pending()
    with session_app.extensions["session_engine"].begin() as conn:
        conn.execute(text("UPDATE weather_forecast_value SET content_id = 77 WHERE id = 1"))
        conn.execute(text("UPDATE weather_forecast_receipt SET version_id = 88"))
    links = discover(f"sqlite:///{tmp_path / 'sessions.db'}")["weather_integrity"]["forecast_links"]
    assert links["values_without_content"] == [1]
    assert len(links["receipts_without_version"]) == 1
    assert links["receipts_without_retrieval"] == []


def test_parking_point_configuration_is_validated_and_kept_distinct():
    from app.services.weather.forecast_archive import ForecastPoint

    assert ForecastPoint.parse(None).id == POINT
    assert ForecastPoint.parse(" 62.81, 22.9 ").id == "62.8100,22.9000"
    for bad in ("62.8", "north,east", "91,22", "62.8,nan"):
        with pytest.raises(ValueError):
            ForecastPoint.parse(bad)


def test_other_points_and_returned_locations_never_merge(forecasts, session_app, client):
    from app.services.weather.forecast_archive import ForecastCollector, ForecastPoint

    collector, fmi, clock = forecasts
    reply = full_run(2)
    fmi.reply = reply
    collector.run_pending()
    # The same reply bytes at a reconfigured point are evidence for that point only.
    moved = ForecastCollector(
        session_app.ctrl, http=fmi, clock=lambda: clock[0], point=ForecastPoint(62.81, 22.9)
    )
    moved.run_pending()
    # FMI snapping the default point to another grid position is a different version.
    clock[0] += 1800
    fmi.reply = full_run(2, pos="62.81000 22.83000")
    collector.run_pending()
    listing = versions(client)["versions"]
    assert [(v["source_id"], v["location"]["pos"]) for v in listing] == [
        (POINT, "62.80000 22.82600"),
        ("62.8100,22.9000", "62.80000 22.82600"),
        (POINT, "62.81000 22.83000"),
    ]
    assert [v["revision"] for v in listing] == [1, 1, 2]
    assert listing[1]["requested_location"] == {"lat": 62.81, "lon": 22.9}
    assert [v["source_id"] for v in versions(client, source_id=POINT)["versions"]] == [POINT] * 2


def test_local_metadata_update_is_a_new_configuration_not_an_fmi_revision(
    forecasts, client, monkeypatch
):
    from app.services.weather import forecast_archive

    collector, fmi, clock = forecasts
    fmi.reply = full_run(2)
    collector.run_pending()
    meta = dict(forecast_archive.PARAMETER_META, Precipitation1h=("mm", "PT1H", "sum"))
    monkeypatch.setattr(forecast_archive, "PARAMETER_META", meta)
    monkeypatch.setattr(forecast_archive, "METADATA_VERSION", "fmi-forecast-meta-next")
    clock[0] += 1800
    collector.run_pending()
    first, second = versions(client)["versions"]
    assert first["request"]["metadata"] == "fmi-forecast-meta-2026-10-16"
    assert second["request"]["metadata"] == "fmi-forecast-meta-next"
    # Same FMI run, each configuration with its own first revision.
    assert [(v["revision"], v["current"]) for v in (first, second)] == [(1, True), (1, True)]
    rain = [
        v for v in detail(client, second["id"])["values"] if v["parameter"] == "Precipitation1h"
    ]
    assert {v["unit"] for v in rain} == {"mm"}


def test_content_reverting_for_one_origin_is_the_latest_revision(forecasts, client):
    collector, fmi, clock = forecasts
    a, b = full_run(2), full_run(2, offset=5)
    for reply in (a, b, a):
        fmi.reply = reply
        collector.run_pending()
        clock[0] += 1800
    listing = versions(client)["versions"]
    assert [(v["revision"], v["current"]) for v in listing] == [(1, False), (2, False), (3, True)]
    # What came back is the same content as revision 1, now received after revision 2.
    assert detail(client, listing[2]["id"])["values"] == detail(client, listing[0]["id"])["values"]
    assert len(listing[2]["receipts"]) == 1
