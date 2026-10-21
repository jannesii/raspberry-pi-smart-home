"""Latest-location lookup behavior and bounded PostgreSQL history reads."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine, event, insert

from app.core._controller.sensors import SensorsMixin
from app.core.schema import esp32_temphum


@pytest.fixture(scope="module")
def postgres_url(tmp_path_factory):
    """Start a disposable cluster with only a private Unix socket, never TCP."""
    initdb = shutil.which("initdb")
    if initdb is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        if not candidates:
            pytest.skip("PostgreSQL server binaries unavailable")
        initdb = str(candidates[-1])
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    work = tmp_path_factory.mktemp("sensor-postgres")
    data = work / "data"
    subprocess.run(
        [initdb, "-D", str(data), "--auth=trust", "--no-locale", "-E", "UTF8"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(work / "postgres.log"),
            "-o",
            f"-k {work} -c listen_addresses='' -c fsync=off",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield URL.create("postgresql+psycopg", database="postgres", query={"host": str(work)})
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
        )


@pytest.fixture(params=["sqlite", "postgresql"])
def sensor_controller(request):
    url = "sqlite://" if request.param == "sqlite" else request.getfixturevalue("postgres_url")
    engine = create_engine(url)
    esp32_temphum.create(engine)
    ctrl = SensorsMixin()
    ctrl._sa_engine = engine
    try:
        yield ctrl
    finally:
        esp32_temphum.drop(engine)
        engine.dispose()


def test_latest_locations_does_not_read_entire_postgres_history(sensor_controller):
    engine = sensor_controller._sa_engine
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL query-plan regression")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO esp32_temphum (location, timestamp, temperature, humidity) "
            "SELECT 'Room ' || room, '2026-10-21T12:00:00+03:00', 20, 40 "
            "FROM generate_series(1, 6) room CROSS JOIN generate_series(1, 10000) sample"
        )
        conn.exec_driver_sql("ANALYZE esp32_temphum")

    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert len(sensor_controller.get_unique_locations()) == 6
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    with engine.connect() as conn:
        plans = [
            conn.exec_driver_sql("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, parameters).scalar()[0]
            for sql, parameters in captured
        ]

    def visited_history_rows(plan):
        own = (
            (plan["Actual Rows"] + plan.get("Rows Removed by Filter", 0)) * plan["Actual Loops"]
            if plan.get("Relation Name") == "esp32_temphum"
            else 0
        )
        return own + sum(visited_history_rows(child) for child in plan.get("Plans", []))

    visited = sum(visited_history_rows(plan["Plan"]) for plan in plans)
    assert visited < 100, f"Latest-location lookup visited {visited} history rows for six locations"


def test_latest_locations_returns_empty_snapshot_without_readings(sensor_controller):
    assert sensor_controller.get_unique_locations() == []


def test_latest_locations_preserves_order_values_and_timestamp_ties(sensor_controller):
    with sensor_controller._sa_engine.begin() as conn:
        conn.execute(
            insert(esp32_temphum),
            [
                {
                    "location": "WC",
                    "timestamp": "2026-10-21T09:00:00+03:00",
                    "temperature": 18,
                    "humidity": 60,
                },
                {
                    "location": "Keittiö",
                    "timestamp": "2026-10-21T12:00:00+03:00",
                    "temperature": 21,
                    "humidity": 40,
                },
                {
                    "location": "Keittiö",
                    "timestamp": "2026-10-21T12:00:00+03:00",
                    "temperature": 22.5,
                    "humidity": 41.5,
                },
                {
                    "location": "WC",
                    "timestamp": "2026-10-21T11:00:00+03:00",
                    "temperature": 19,
                    "humidity": 58,
                },
                {
                    "location": "Keittiö",
                    "timestamp": "2026-10-20T12:00:00+03:00",
                    "temperature": 99,
                    "humidity": 99,
                },
            ],
        )
    assert sensor_controller.get_unique_locations() == [
        {
            "location": "Keittiö",
            "timestamp": "2026-10-21T12:00:00+03:00",
            "temperature": 22.5,
            "humidity": 41.5,
        },
        {
            "location": "WC",
            "timestamp": "2026-10-21T11:00:00+03:00",
            "temperature": 19.0,
            "humidity": 58.0,
        },
    ]


def test_latest_locations_observes_new_readings_and_locations(sensor_controller):
    with sensor_controller._sa_engine.begin() as conn:
        conn.execute(
            insert(esp32_temphum),
            {
                "location": "WC",
                "timestamp": "2026-10-21T10:00:00+03:00",
                "temperature": 18,
                "humidity": 60,
            },
        )
    assert len(sensor_controller.get_unique_locations()) == 1
    with sensor_controller._sa_engine.begin() as conn:
        conn.execute(
            insert(esp32_temphum),
            [
                {
                    "location": "WC",
                    "timestamp": "2026-10-21T11:00:00+03:00",
                    "temperature": 19,
                    "humidity": 58,
                },
                {
                    "location": "Keittiö",
                    "timestamp": "2026-10-21T11:00:00+03:00",
                    "temperature": 22,
                    "humidity": 40,
                },
            ],
        )
    assert sensor_controller.get_unique_locations() == [
        {
            "location": "Keittiö",
            "timestamp": "2026-10-21T11:00:00+03:00",
            "temperature": 22.0,
            "humidity": 40.0,
        },
        {
            "location": "WC",
            "timestamp": "2026-10-21T11:00:00+03:00",
            "temperature": 19.0,
            "humidity": 58.0,
        },
    ]
