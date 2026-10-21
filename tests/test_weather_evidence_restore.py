"""Whole-database backup and isolated restore of weather evidence beside heater data.

Collection runs through the real collectors, parser, recovery and archive with
a controlled FMI and clock. The backup and restore use the documented tools
(``scripts/heater_history.py`` for SQLite, ``pg_dump``/``pg_restore`` for a
private PostgreSQL cluster) into a separate target; the restored copy then
serves the same evidence through the authenticated API and resumes collection.
"""

import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
import test_car_heater_sessions as evidence
import test_weather_forecast_archive as forecast_suite
import test_weather_observation_recovery as recovery
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres
from test_car_heater_photos import JPEG, upload
from test_weather_archive_migration import run_migration
from test_weather_evidence_export import new_session, run_with
from test_weather_observation_archive import PELMAA

from app.core import Controller
from app.core.schema import metadata

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers
station = recovery.station
forecasts = forecast_suite.forecasts

START = recovery.START
# The heater evidence these flows touch, as in the photo restore check.
HEATER_TABLES = (
    "car_heater_sensor_frame",
    "car_heater_session",
    "car_heater_execution",
    "car_heater_departure_plan",
    "car_heater_photo",
    "car_heater_observation",
)
# As in the preservation guide: portable, and restore stops at the first error.
PORTABLE = ("--no-owner", "--no-acl")
WEATHER_MIGRATIONS = (
    "20261016_0016_weather_observations",
    "20261016_0017_weather_forecasts",
    "20261017_0018_weather_observation_recovery",
)


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request, session_app, tmp_path, monkeypatch):
    """Point the app at the source database; yield (source URL, restore())."""
    if request.param == "sqlite":
        source = tmp_path / "sessions.db"

        def restore():
            backup, restored = tmp_path / "backup.db", tmp_path / "restored.db"
            for frm, to in [(source, backup), (backup, restored)]:
                subprocess.run(
                    [sys.executable, "scripts/heater_history.py", "sqlite-copy", str(frm), str(to)],
                    check=True,
                    capture_output=True,
                )
            return f"sqlite:///{restored}"

        yield f"sqlite:///{source}", restore
        return

    binaries, socket = request.getfixturevalue("isolated_postgres")
    url = URL.create(
        "postgresql+psycopg", database="postgres", query={"host": str(socket), "port": "55439"}
    )
    engine = create_engine(url)
    metadata.create_all(engine, tables=[metadata.tables[name] for name in HEATER_TABLES])
    # The weather schema comes from the reviewed migrations, as deployed.
    with engine.begin() as conn:
        for name in WEATHER_MIGRATIONS:
            run_migration(conn, monkeypatch, name, "upgrade")
    original = session_app.ctrl._sa_engine
    session_app.ctrl._sa_engine = engine
    session_app.extensions["session_engine"] = engine

    def restore():
        pg = ["-h", str(socket), "-p", "55439"]
        archive = tmp_path / "weather.dump"
        subprocess.run(
            [
                str(binaries / "pg_dump"),
                *pg,
                "-d",
                "postgres",
                "-Fc",
                *PORTABLE,
                "-f",
                str(archive),
            ],
            check=True,
            capture_output=True,
        )
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("CREATE DATABASE restored")
        subprocess.run(
            [
                str(binaries / "pg_restore"),
                *pg,
                "-d",
                "restored",
                *PORTABLE,
                "--exit-on-error",
                str(archive),
            ],
            check=True,
            capture_output=True,
        )
        return url.set(database="restored").render_as_string(hide_password=False)

    try:
        yield url.render_as_string(hide_password=False), restore
    finally:
        session_app.ctrl._sa_engine = original
        engine.dispose()


def evidence_snapshot(client, session_id):
    """Everything the owner can retrieve about the session's weather and the archive."""
    weather = client.get(f"/api/car_heater/sessions/{session_id}/weather").get_json()
    observations = {
        "kind": "observation",
        "start": (START - timedelta(days=1)).isoformat(),
        "end": (START + timedelta(hours=1)).isoformat(),
    }
    forecasts = {
        "kind": "forecast",
        "start": (START - timedelta(hours=3)).isoformat(),
        "end": (START + timedelta(hours=3)).isoformat(),
    }
    snapshot = {"session_weather": weather}
    for name, query in (("observations", observations), ("forecasts", forecasts)):
        for fmt in ("json", "csv"):
            response = client.get(
                "/api/car_heater/weather/export", query_string={**query, "format": fmt}
            )
            assert response.status_code == 200, response.get_data(as_text=True)
            snapshot[f"{name}.{fmt}"] = response.get_data(as_text=True)
    obs = client.get(
        "/api/car_heater/weather/export", query_string={**observations, "format": "json"}
    ).get_json()
    fc = client.get(
        "/api/car_heater/weather/export", query_string={**forecasts, "format": "json"}
    ).get_json()
    reply_ids = {r["reply_id"] for r in obs["rows"]}
    reply_ids |= {r["reply_id"] for v in fc["versions"] for r in v["receipts"]}
    snapshot["replies"] = {
        reply_id: client.get(f"/api/car_heater/weather/replies/{reply_id}").data
        for reply_id in sorted(reply_ids)
    }
    return snapshot


def test_isolated_restore_serves_the_same_weather_evidence_and_resumes_collection(
    backend, station, forecasts, session_app, client
):
    from scripts.heater_history import discover

    from app.services.weather.forecast_archive import ForecastCollector
    from app.services.weather.observation_archive import ObservationCollector

    source_url, restore = backend
    build, fmi, clock = station
    forecaster, forecast_fmi, forecast_clock = forecasts
    collector = build()
    zero = START - timedelta(minutes=30)
    fmi.overrides[(PELMAA, zero)] = "0.0"
    temperatures = ["0.0", "NaN", "-1.5", "-2.0", "-2.5", "-3.0"]
    forecast_fmi.reply = run_with(temperatures)
    collector.run_pending()
    forecaster.run_pending()
    clock[0] += 1800
    forecast_clock[0] += 1800
    fmi.overrides[(PELMAA, zero)] = "0.1"
    forecast_fmi.reply = run_with([*temperatures[:3], "-2.2", "-2.5", "-3.0"])
    collector.run_pending()
    forecaster.run_pending()
    session_id = new_session(session_app, client, START - timedelta(hours=1))
    auth = headers(session_app, client)
    photo_id = str(uuid4())
    # Photo times are checked against the real clock, so they keep the photo suite's dates.
    saved = upload(client, auth, session_id, photo_id)
    assert saved.status_code == 201, saved.get_json()
    detail = client.get(f"/api/car_heater/sessions/{session_id}").get_json()
    before = evidence_snapshot(client, session_id)
    assert len(before["replies"]) >= 4
    source_report = discover(source_url)

    restored_url = restore()

    report = discover(restored_url)
    for table in ("car_heater_session", "car_heater_photo", *source_report["weather_fingerprints"]):
        assert report["tables"][table] == source_report["tables"][table], table
    assert report["weather_fingerprints"] == source_report["weather_fingerprints"]
    assert set(report["weather_fingerprints"]) >= {
        "weather_reply",
        "weather_retrieval",
        "weather_observation",
        "weather_collection_progress",
        "weather_forecast_content",
        "weather_forecast_value",
        "weather_forecast_version",
        "weather_forecast_receipt",
    }
    integrity = report["weather_integrity"]
    assert integrity["replies_checked"] == len(before["replies"])
    for key in (
        "mismatched_replies",
        "retrievals_without_reply",
        "successful_retrievals_without_reply",
        "readings_without_retrieval",
        "progress_without_retrievals",
    ):
        assert integrity[key] == [], key
    assert all(links == [] for links in integrity["forecast_links"].values())
    assert {p["source_id"] for p in integrity["collection_progress"]} == {PELMAA, "137188"}
    assert report["photo_integrity"] == {"checked": 1, "mismatched": [], "orphaned": []}

    # A fresh process on the restored copy: same evidence through the same API.
    restored = create_engine(restored_url)
    try:
        session_app.ctrl = Controller(db_path=session_app.config["DB_PATH"])
        session_app.ctrl._sa_engine = restored
        resumed = ObservationCollector(session_app.ctrl, http=fmi, clock=lambda: clock[0])
        session_app.weather_collector = resumed
        session_app.weather_forecast_collector = ForecastCollector(
            session_app.ctrl, http=forecast_fmi, clock=lambda: forecast_clock[0]
        )
        assert evidence_snapshot(client, session_id) == before
        assert client.get(f"/api/car_heater/sessions/{session_id}").get_json() == detail
        photo = client.get(f"/api/car_heater/sessions/{session_id}/photos/{photo_id}")
        assert photo.status_code == 200 and photo.data == JPEG

        progress = {
            s["source_id"]: s["history"]
            for s in client.get("/api/car_heater/weather/health").get_json()["sources"]
        }
        # Restart continues from the restored checkpoints; it never re-initializes.
        clock[0] += 600
        seen = len(fmi.requests)
        resumed.run_pending()
        windows = fmi.windows(since=seen)
        assert windows and all(hi - lo == timedelta(hours=2) for lo, hi in windows)
        after = {
            s["source_id"]: s["history"]
            for s in client.get("/api/car_heater/weather/health").get_json()["sources"]
        }
        for source_id, history in progress.items():
            assert after[source_id]["history_start"] == history["history_start"]
            assert after[source_id]["initialized_at"] == history["initialized_at"]
        newest = START + timedelta(minutes=40)
        rows = client.get(
            "/api/car_heater/weather/observations",
            query_string={"start": newest.isoformat(), "end": newest.isoformat()},
        ).get_json()["readings"]
        assert {r["fmisid"] for r in rows} == {PELMAA, "137188"}
    finally:
        restored.dispose()


def test_discovery_detects_changed_rows_and_broken_checkpoints(station, session_app, tmp_path):
    from scripts.heater_history import discover
    from sqlalchemy import text

    build, _fmi, _clock = station
    build().run_pending()
    url = f"sqlite:///{tmp_path / 'sessions.db'}"
    before = discover(url)
    assert discover(url)["weather_fingerprints"] == before["weather_fingerprints"]
    with session_app.extensions["session_engine"].begin() as conn:
        # Same row count, one value silently different.
        conn.execute(text("UPDATE weather_observation SET value = value + 1 WHERE id = 1"))
        conn.execute(text("UPDATE weather_retrieval SET reply_id = NULL WHERE id = 1"))
        conn.execute(
            text(
                "DELETE FROM weather_observation WHERE retrieval_id IN "
                "(SELECT id FROM weather_retrieval WHERE source_id = '137188')"
            )
        )
        conn.execute(text("DELETE FROM weather_retrieval WHERE source_id = '137188'"))
    after = discover(url)
    prints = after["weather_fingerprints"]
    assert (
        prints["weather_observation"]["sha256"]
        != before["weather_fingerprints"]["weather_observation"]["sha256"]
    )
    assert prints["weather_reply"] == before["weather_fingerprints"]["weather_reply"]
    integrity = after["weather_integrity"]
    assert integrity["successful_retrievals_without_reply"] == [1]
    assert integrity["progress_without_retrievals"] == [
        {"kind": "observation", "source_id": "137188"}
    ]
