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
    migration = importlib.import_module("migrations.versions.20261016_0016_weather_observations")
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
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
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.downgrade()
        assert len(conn.execute(car_heater_session.select()).all()) == 1
    assert not set(WEATHER_TABLES) & set(inspect(engine).get_table_names())
