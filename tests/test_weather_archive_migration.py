"""Reviewed weather-archive migration on isolated SQLite and PostgreSQL databases."""

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres
from test_weather_observation_archive import AIRPORT, PELMAA, START, FakeFMI, every_ten, fmi_reply

from app.core.schema import car_heater_session, metadata

WEATHER_TABLES = ("weather_reply", "weather_retrieval", "weather_observation")
# The current collector writes the schema at the head of the weather chain.
LATER = ("20261016_0017_weather_forecasts", "20261017_0018_weather_observation_recovery")


def run_migration(conn, monkeypatch, name, step):
    migration = importlib.import_module(f"migrations.versions.{name}")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
    getattr(migration, step)()


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_engine(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'migration.db'}"
    else:
        _binaries, socket = request.getfixturevalue("isolated_postgres")
        url = URL.create(
            "postgresql+psycopg", database="postgres", query={"host": str(socket), "port": "55439"}
        )
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_migration_matches_schema_preserves_evidence_and_accepts_collection(
    migration_engine, monkeypatch, tmp_path
):
    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.services.weather.observation_archive import ObservationCollector

    engine = migration_engine
    metadata.create_all(engine, tables=[car_heater_session])
    with engine.begin() as conn:
        conn.execute(
            car_heater_session.insert().values(
                id="kept",
                trigger="manual",
                created_at="2027-01-15T12:00:00+00:00",
                observed_at="2027-01-15T12:00:00+00:00",
                snapshot="{}",
            )
        )
    with engine.begin() as conn:
        run_migration(conn, monkeypatch, "20261016_0016_weather_observations", "upgrade")
        for name in LATER:
            run_migration(conn, monkeypatch, name, "upgrade")
    inspector = inspect(engine)
    for name in WEATHER_TABLES:
        migrated = {c["name"] for c in inspector.get_columns(name)}
        assert migrated == {c.name for c in metadata.tables[name].c}, name
    assert {i["name"] for i in inspector.get_indexes("weather_observation")} >= {
        "idx_weather_observation_time"
    }

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    fmi = FakeFMI()
    fmi.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-5.0", "NaN"])})
    fmi.replies[AIRPORT] = fmi_reply(AIRPORT, {"vis": every_ten(START, ["5590.0"])})
    collector = ObservationCollector(ctrl, http=fmi, clock=lambda: START.timestamp())
    collector.run_pending()
    rows = ctrl.list_weather_observations("2027-01-15T00:00:00+00:00", "2027-01-16T00:00:00+00:00")
    assert [(r["fmisid"], r["parameter"], r["status"]) for r in rows] == [
        (PELMAA, "t2m", "valid"),
        (PELMAA, "t2m", "missing"),
        (AIRPORT, "vis", "valid"),
    ]
    assert ctrl.get_weather_reply(rows[-1]["reply_id"])["content"] == fmi.replies[AIRPORT]
    ctrl.db.conn.close()

    with engine.begin() as conn:
        for name in reversed(LATER):
            run_migration(conn, monkeypatch, name, "downgrade")
        run_migration(conn, monkeypatch, "20261016_0016_weather_observations", "downgrade")
        assert len(conn.execute(car_heater_session.select()).all()) == 1
    assert not set(WEATHER_TABLES) & set(inspect(engine).get_table_names())


def insert_live_only_readings(conn, slots, stations=(PELMAA, AIRPORT)):
    """Live-window evidence as the #24 collector stored it, through the table as migrated."""
    import json
    from datetime import timedelta

    from sqlalchemy import MetaData, Table

    retrievals = Table("weather_retrieval", MetaData(), autoload_with=conn)
    readings = Table("weather_observation", MetaData(), autoload_with=conn)
    for slot in slots:
        for fmisid in stations:
            retrieval_id = conn.execute(
                retrievals.insert()
                .values(
                    kind="observation",
                    source_id=fmisid,
                    intended_at=slot.isoformat(),
                    requested_start=(slot - timedelta(hours=2)).isoformat(),
                    requested_end=slot.isoformat(),
                    started_at=slot.isoformat(),
                    retrieved_at=slot.isoformat(),
                    status="ok",
                    summary=json.dumps({"series": 1, "readings": 1, "missing": 0, "invalid": 0}),
                )
                .returning(retrievals.c.id)
            ).scalar_one()
            conn.execute(
                readings.insert().values(
                    fmisid=fmisid,
                    parameter="t2m",
                    observed_at=slot.isoformat(),
                    revision=1,
                    status="valid",
                    value=-5.0,
                    source_value="-5.0",
                    unit="degC",
                    period="PT1M",
                    retrieval_id=retrieval_id,
                )
            )


def test_recovery_migration_labels_live_rows_and_upgraded_archive_initializes_history(
    migration_engine, monkeypatch, tmp_path
):
    from datetime import timedelta

    from test_weather_observation_recovery import StationFMI

    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.services.weather.observation_archive import ObservationCollector

    engine = migration_engine
    metadata.create_all(engine, tables=[car_heater_session])
    first_live = START - timedelta(hours=10)
    with engine.begin() as conn:
        run_migration(conn, monkeypatch, "20261016_0016_weather_observations", "upgrade")
        run_migration(conn, monkeypatch, "20261016_0017_weather_forecasts", "upgrade")
        # #24 ran live-only from first_live; its rows do not prove a 24-hour history.
        insert_live_only_readings(conn, [first_live, START - timedelta(minutes=10)])
    with engine.begin() as conn:
        run_migration(conn, monkeypatch, LATER[-1], "upgrade")
    inspector = inspect(engine)
    for name in ("weather_retrieval", "weather_collection_progress"):
        migrated = {c["name"] for c in inspector.get_columns(name)}
        assert migrated == {c.name for c in metadata.tables[name].c}, name
    with engine.connect() as conn:
        purposes = conn.exec_driver_sql("SELECT DISTINCT purpose FROM weather_retrieval")
        assert purposes.scalars().all() == ["live"]
        progress = conn.exec_driver_sql("SELECT COUNT(*) FROM weather_collection_progress")
        assert progress.scalar() == 0

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    fmi = StationFMI()
    ObservationCollector(ctrl, http=fmi, clock=lambda: START.timestamp()).run_pending()
    progress = ctrl.list_weather_collection_progress()
    for fmisid in (PELMAA, AIRPORT):
        assert progress[fmisid]["origin"] == "upgraded"
        assert progress[fmisid]["history_start"] == (first_live - timedelta(hours=24)).isoformat()
        history = fmi.windows(fmisid)[1:]
        # Initialization fetches the day before the first live-only collection.
        assert min(lo for lo, _ in history) == first_live - timedelta(hours=24)
    rows = ctrl.list_weather_observations(
        (first_live - timedelta(hours=24)).isoformat(), START.isoformat()
    )
    kept = [r for r in rows if r["observed_at"] == first_live.isoformat() and r["revision"] == 1]
    assert {(r["fmisid"], r["value"], r["purpose"]) for r in kept} == {
        (PELMAA, -5.0, "live"),
        (AIRPORT, -5.0, "live"),
    }
    count = len(rows)
    ctrl.db.conn.close()

    with engine.begin() as conn:
        run_migration(conn, monkeypatch, LATER[-1], "downgrade")
    assert "weather_collection_progress" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        # Backfilled evidence survives the downgrade.
        total = conn.exec_driver_sql("SELECT COUNT(*) FROM weather_observation").scalar()
        assert total == count
