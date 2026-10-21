"""Session weather coverage, bounded exports and original replies through public seams.

The FMI HTTP boundary and clock are controlled; the collectors, parsing,
recovery, persistence and authenticated endpoints are the real ones, on an
isolated database. No reading is inserted by hand.
"""

from datetime import datetime, timedelta

import requests
import test_car_heater_sessions as evidence
import test_departure_heating
import test_weather_forecast_archive as forecast_suite
import test_weather_observation_recovery as recovery
from test_weather_observation_archive import AIRPORT, PELMAA

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
departure = test_departure_heating.departure
station = recovery.station
forecasts = forecast_suite.forecasts

START = recovery.START


def new_session(session_app, client, observed_at, minutes=60):
    response = client.post(
        "/api/car_heater/sessions",
        json={
            "trigger": "manual",
            "observed_at": observed_at.isoformat(),
            "heating_duration_minutes": minutes,
        },
        headers=headers(session_app, client),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def session_weather(client, session_id):
    response = client.get(f"/api/car_heater/sessions/{session_id}/weather")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_session_coverage_reports_its_window_backfill_and_honest_station_gaps(
    station, session_app, client
):
    build, fmi, _clock = station
    fmi.failing[AIRPORT] = requests.Timeout("slow")
    build().run_pending()
    # Heating began five hours before the collector first ran: only the
    # initialization backfill can cover it.
    session_id = new_session(session_app, client, START - timedelta(hours=5))
    detail = client.get(f"/api/car_heater/sessions/{session_id}").get_json()

    data = session_weather(client, session_id)

    assert data["window"] == {
        "start": "2027-01-14T21:30:00+00:00",
        "end": "2027-01-14T23:15:00+00:00",
        "heating_anchor": "2027-01-14T22:00:00+00:00",
        "anchor_source": "session",
        "end_recorded": True,
    }
    assert data["checked_until"] == "2027-01-14T23:15:00+00:00"
    stations = {s["source_id"]: s for s in data["stations"]}
    pelmaa, airport = stations[PELMAA], stations[AIRPORT]
    assert pelmaa["station_name"] == "Seinäjoki Pelmaa"
    assert pelmaa["segments"] == [
        {
            "start": "2027-01-14T21:30:00+00:00",
            "end": "2027-01-14T23:15:00+00:00",
            "kind": "collected",
        }
    ]
    # 21:30 … 23:10 every ten minutes, all recovered by initialization.
    assert pelmaa["readings"] == {"current": 11, "revised": 0, "backfilled": 11}
    assert pelmaa["health"]["state"] == "collecting"
    [reply] = pelmaa["replies"]
    assert reply["purpose"] == "initialization"
    original = client.get(f"/api/car_heater/weather/replies/{reply['reply_id']}")
    assert original.status_code == 200
    assert b"2027-01-14T21:30:00Z" in original.data

    # The failed station is not filled from the other one: a repairable gap.
    assert airport["segments"] == [
        {
            "start": "2027-01-14T21:30:00+00:00",
            "end": "2027-01-14T23:15:00+00:00",
            "kind": "not_collected",
        }
    ]
    assert airport["readings"] == {"current": 0, "revised": 0, "backfilled": 0}
    assert airport["replies"] == []
    assert airport["health"]["state"] == "failing"
    assert airport["health"]["last_error"] == "timeout"

    # Coverage is read-only: the session's own evidence is unchanged.
    assert client.get(f"/api/car_heater/sessions/{session_id}").get_json() == detail
    assert set(data["exports"]) == {"observations", "forecasts", "coverage"}


def export(client, **query):
    response = client.get("/api/car_heater/weather/export", query_string=query)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def test_observation_export_keeps_zero_missing_corrections_and_backfill_apart(station, client):
    import csv
    import io

    build, fmi, clock = station
    zero, missing = START - timedelta(minutes=30), START - timedelta(minutes=20)
    fmi.overrides[(PELMAA, zero)] = "0.0"
    fmi.overrides[(PELMAA, missing)] = "NaN"
    collector = build()
    collector.run_pending()
    # The next live window returns a correction for the zero reading.
    clock[0] += 600
    fmi.overrides[(PELMAA, zero)] = "0.1"
    collector.run_pending()
    span = {
        "kind": "observation",
        "start": (START - timedelta(hours=3)).isoformat(),
        "end": START.isoformat(),
        "fmisid": PELMAA,
    }

    data = export(client, format="json", **span).get_json()
    rows = [r for r in data["rows"] if r["parameter"] == "t2m"]
    at = {}
    for row in rows:
        at.setdefault(row["observed_at"], []).append(row)
    first, corrected = at[zero.isoformat()]
    assert (first["value"], first["status"], first["revision"], first["current"]) == (
        0.0,
        "valid",
        1,
        False,
    )
    assert (corrected["value"], corrected["revision"], corrected["current"]) == (0.1, 2, True)
    assert corrected["retrieved_at"] > first["retrieved_at"]
    [gap] = at[missing.isoformat()]
    assert (gap["value"], gap["status"], gap["source_value"]) == (None, "missing", "NaN")
    [old] = at[(START - timedelta(hours=3)).isoformat()]
    assert (old["purpose"], old["backfill"]) == ("initialization", True)
    [live] = at[START.isoformat()]
    assert (live["purpose"], live["backfill"]) == ("live", False)
    assert live["station_pos"] == "62.93808 22.48878"
    for key in ("fmisid", "station_name", "unit", "period", "property_href", "retrieval_id"):
        assert live[key] is not None
    assert live["reply_id"] is not None
    assert [s["source_id"] for s in data["coverage"]] == [PELMAA]
    assert data["coverage"][0]["segments"][-1]["kind"] == "collected"

    response = export(client, format="csv", **span)
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"
    table = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert len(table) == len(data["rows"])
    by_time = {}
    for row in table:
        if row["parameter"] == "t2m":
            by_time.setdefault(row["observed_at"], []).append(row)
    first, corrected = by_time[zero.isoformat()]
    # A real zero keeps its number; a received missing value has none.
    assert (first["value"], first["source_value"], first["current"]) == ("0.0", "0.0", "false")
    assert (corrected["value"], corrected["revision"]) == ("0.1", "2")
    [gap] = by_time[missing.isoformat()]
    assert (gap["value"], gap["status"]) == ("", "missing")
    assert by_time[(START - timedelta(hours=3)).isoformat()][0]["backfill"] == "true"

    # What an earlier decision could have seen: before the correction arrived.
    before = export(client, format="json", as_of=START.isoformat(), **span).get_json()
    [held] = [r for r in before["rows"] if r["observed_at"] == zero.isoformat()]
    assert (held["value"], held["current"]) == (0.0, True)


def test_exports_are_bounded_validated_and_authenticated(station, session_app, client):
    build, _fmi, _clock = station
    build().run_pending()
    week = {"start": "2027-01-01T00:00:00+00:00", "end": "2027-01-09T00:00:00+00:00"}
    two_days = {"start": "2027-01-12T00:00:00+00:00", "end": "2027-01-15T00:00:00+00:00"}
    for query in (
        {"kind": "observation", "format": "csv", **week},
        {"kind": "forecast", "format": "json", **two_days},
        {"kind": "everything", "format": "csv"},
        {"kind": "observation", "format": "xlsx"},
        {"kind": "observation", "format": "csv", "start": "2027-01-15T00:00:00"},
        {"kind": "observation", "format": "csv", "start_date": "15.1.2027"},
        {
            "kind": "observation",
            "format": "csv",
            "start_date": "2027-01-16",
            "end_date": "2027-01-15",
        },
    ):
        response = client.get("/api/car_heater/weather/export", query_string=query)
        assert response.status_code == 400, query
        assert response.is_json and response.get_json()["error"]
    # Helsinki calendar days, inclusive.
    data = export(
        client, kind="observation", format="json", start_date="2027-01-14", end_date="2027-01-15"
    ).get_json()
    assert (data["start"], data["end"]) == (
        "2027-01-13T22:00:00+00:00",
        "2027-01-15T22:00:00+00:00",
    )
    # Seven calendar days across the autumn clock change are 169 hours, still allowed.
    week = {"kind": "observation", "format": "json", "end_date": "2027-10-31"}
    assert export(client, start_date="2027-10-25", **week).get_json()["end"] == (
        "2027-10-31T22:00:00+00:00"
    )
    too_long = client.get(
        "/api/car_heater/weather/export", query_string={"start_date": "2027-10-24", **week}
    )
    assert too_long.status_code == 400
    anonymous = session_app.test_client()
    assert (
        anonymous.get(
            "/api/car_heater/weather/export", query_string={"kind": "observation", "format": "csv"}
        ).status_code
        == 401
    )


def run_with(temperatures, offset=0.0, **kwargs):
    """A six-hour run whose Temperature is given; other fields count up."""
    first = forecast_suite.ORIGIN + timedelta(hours=1)
    series = {
        p: forecast_suite.hourly(first, [f"{n + offset + h / 10:.1f}" for h in range(6)])
        for n, p in enumerate(forecast_suite.FIELDS)
    }
    series["Temperature"] = forecast_suite.hourly(first, temperatures)
    return forecast_suite.forecast_reply(series, **kwargs)


def test_session_forecast_evidence_and_export_keep_versions_as_received(
    forecasts, session_app, client
):
    import csv
    import io

    collector, fmi, clock = forecasts
    temperatures = ["0.0", "NaN", "-1.5", "-2.0", "-2.5", "-3.0"]
    fmi.reply = run_with(temperatures)
    collector.run_pending()
    clock[0] += 1800
    fmi.reply = run_with(temperatures, stamp="again")
    collector.run_pending()
    clock[0] += 1800
    # The same model origin, revised: a new revision beside the first.
    fmi.reply = run_with([*temperatures[:3], "-2.2", "-2.5", "-3.0"])
    collector.run_pending()
    session_id = new_session(session_app, client, START + timedelta(minutes=40))

    forecast = session_weather(client, session_id)["forecast"]

    assert forecast["source_id"] == forecast_suite.POINT
    assert forecast["health"]["state"] == "collecting"
    [held] = forecast["held_at_anchor"]
    assert (held["revision"], len(held["receipts"])) == (1, 2)
    first, revised = forecast["received_in_window"]
    assert (first["id"], first["current"]) == (held["id"], False)
    assert (revised["revision"], revised["current"]) == (2, True)
    assert forecast["unobserved"] == []
    for version in (first, revised):
        assert "values" not in version
        reply = version["receipts"][-1]["reply_id"]
        assert client.get(f"/api/car_heater/weather/replies/{reply}").status_code == 200

    span = {
        "kind": "forecast",
        "start": (START - timedelta(hours=3)).isoformat(),
        "end": (START + timedelta(hours=3)).isoformat(),
    }
    data = export(client, format="json", **span).get_json()
    assert [v["id"] for v in data["versions"]] == [first["id"], revised["id"]]
    temperature = {
        (r["revision"], r["valid_at"]): r for r in data["rows"] if r["parameter"] == "Temperature"
    }
    one = (START - timedelta(hours=2)).isoformat()
    two = (START - timedelta(hours=1)).isoformat()
    four = (START + timedelta(hours=1)).isoformat()
    assert (temperature[1, one]["value"], temperature[1, one]["status"]) == (0.0, "valid")
    assert (temperature[1, two]["value"], temperature[1, two]["status"]) == (None, "missing")
    assert (temperature[1, four]["value"], temperature[2, four]["value"]) == (-2.0, -2.2)
    row = temperature[2, four]
    assert row["analysis_time"] == forecast_suite.ORIGIN.isoformat()
    assert row["procedure"] == forecast_suite.PROCEDURE
    assert (row["requested_lat"], row["requested_lon"]) == (62.8, 22.826)
    assert (row["unit"], row["location_name"]) == ("degC", "Seinäjoki")
    assert row["first_retrieved_at"] == (START + timedelta(hours=1)).isoformat()
    assert row["result_time"] and row["first_reply_id"]
    assert (temperature[1, four]["receipts"], temperature[1, four]["current"]) == (2, False)

    table = list(
        csv.DictReader(io.StringIO(export(client, format="csv", **span).get_data(as_text=True)))
    )
    assert len(table) == len(data["rows"]) == 2 * 6 * len(forecast_suite.FIELDS)
    cells = {(r["revision"], r["valid_at"]): r for r in table if r["parameter"] == "Temperature"}
    assert (cells["1", one]["value"], cells["1", two]["value"]) == ("0.0", "")
    assert cells["1", two]["status"] == "missing"

    # Before the revision arrived only the first version existed.
    earlier = export(
        client, format="json", as_of=(START + timedelta(minutes=40)).isoformat(), **span
    ).get_json()
    [only] = earlier["versions"]
    assert (only["id"], only["current"], len(only["receipts"])) == (first["id"], True, 2)


def test_weather_evidence_access_never_touches_heating_or_saved_snapshots(
    departure, session_app, client
):
    import time

    from test_weather_observation_archive import Response

    from app.services.weather.observation_archive import ObservationCollector

    _planner, heater, clock, _observation = departure
    auth = headers(session_app, client)
    plan = client.post(
        "/api/car_heater/departure", json={"departure_at": "2027-01-16T08:00"}, headers=auth
    ).get_json()
    session_id = plan["session_id"]
    fmi = recovery.StationFMI()
    # One station's replies cannot be parsed; the other is archived normally.
    fmi.failing[AIRPORT] = Response(200, b"<html>maintenance</html>")
    collector = ObservationCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
    session_app.weather_collector = collector
    collector.run_pending()
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
    detail = client.get(f"/api/car_heater/sessions/{session_id}").get_json()
    relay = heater.get_bounded_session()
    # The collection due at the planned start cannot be archived; that stays
    # inside the collector.
    record = session_app.ctrl.record_weather_observation_retrieval

    def failing_write(*args, **kwargs):
        raise RuntimeError("disk full")

    session_app.ctrl.record_weather_observation_retrieval = failing_write
    assert collector.run_pending() is True
    session_app.ctrl.record_weather_observation_retrieval = record

    data = session_weather(client, session_id)
    for url in (*data["exports"]["observations"].values(), *data["exports"]["forecasts"].values()):
        assert client.get(url).status_code == 200

    assert data["window"]["anchor_source"] in ("execution", "departure_plan")
    stations = {s["source_id"]: s for s in data["stations"]}
    assert stations[AIRPORT]["health"]["last_error"] == "malformed_reply"
    assert stations[PELMAA]["health"]["archive_error"] is not None
    # Delivery drains the queue: evidence access queued nothing and changed
    # no saved plan or snapshot.
    assert heater.get_queued_commands() == []
    assert client.get(f"/api/car_heater/sessions/{session_id}").get_json() == detail
    assert heater.get_bounded_session() == relay
    saved = client.get("/api/car_heater/departure").get_json()
    # The planner keeps its own observed-temperature source: -10 °C gives 150 minutes.
    assert (saved["finish_at"], saved["preheat_minutes"]) == (plan["finish_at"], 150)
    started = time.monotonic()
    assert client.delete("/api/car_heater/bounded", headers=auth).status_code == 200
    assert time.monotonic() - started < 1
    assert [c["action"] for c in heater.get_queued_commands()] == ["turn_off"]


def test_session_weather_and_replies_fail_closed(station, session_app, client):
    build, _fmi, _clock = station
    build().run_pending()
    session_id = new_session(session_app, client, START - timedelta(hours=1))
    [reply] = session_weather(client, session_id)["stations"][0]["replies"][:1]
    anonymous = session_app.test_client()
    for path in (
        f"/api/car_heater/sessions/{session_id}/weather",
        f"/api/car_heater/weather/replies/{reply['reply_id']}",
    ):
        assert anonymous.get(path).status_code == 401
    assert client.get("/api/car_heater/sessions/unknown/weather").status_code == 404
    # Identifiers are bounded integers; nothing outside the archive's range is looked up.
    for bad in ("0", "-1", "abc", str(2**63), "1.5"):
        assert client.get(f"/api/car_heater/weather/replies/{bad}").status_code == 404
    with session_app.extensions["session_engine"].begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text("UPDATE weather_reply SET sha256 = :bad WHERE id = :id"),
            {"bad": "0" * 64, "id": reply["reply_id"]},
        )
    damaged = client.get(f"/api/car_heater/weather/replies/{reply['reply_id']}")
    assert damaged.status_code == 500
    assert b"FeatureCollection" not in damaged.data


def test_coverage_export_lists_station_gaps_and_unchecked_forecast_stretches(
    station, forecasts, client
):
    import csv
    import io

    build, fmi, _clock = station
    fmi.failing[AIRPORT] = requests.Timeout("slow")
    build().run_pending()
    forecaster, forecast_fmi, _forecast_clock = forecasts
    forecast_fmi.reply = run_with(["1.0"] * 6)
    forecaster.run_pending()

    response = export(
        client, kind="coverage", format="csv", start_date="2027-01-15", end_date="2027-01-15"
    )

    # Named by the Helsinki dates chosen, not their UTC instants.
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="weather-coverage-2027-01-15-2027-01-15.csv"'
    )
    rows = {
        (r["source_kind"], r["source_id"], r["start"], r["end"]): r
        for r in csv.DictReader(io.StringIO(response.get_data(as_text=True)))
    }
    day_start = "2027-01-14T22:00:00+00:00"
    live = (START - timedelta(hours=2)).isoformat()
    assert rows["observation", AIRPORT, day_start, live]["state"] == "not_collected"
    assert rows["observation", AIRPORT, live, START.isoformat()]["state"] == "failed"
    assert rows["observation", PELMAA, day_start, START.isoformat()]["state"] == "collected"
    stretch = rows["forecast", forecast_suite.POINT, day_start, START.isoformat()]
    assert (stretch["state"], stretch["failed_attempts"]) == ("unchecked", "0")
    # Nothing after the collector's clock is reported as missing yet.
    assert max(r["end"] for r in rows.values()) == START.isoformat()
