"""Reviewed forecast-archive migration on isolated SQLite and PostgreSQL databases."""

import importlib

import test_weather_archive_migration as observation_migration
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres
from test_weather_forecast_archive import START, FakeForecastFMI, full_run
from test_weather_observation_archive import AIRPORT, PELMAA, FakeFMI, every_ten, fmi_reply

from app.core.schema import car_heater_session, metadata

migration_engine = observation_migration.migration_engine

FORECAST_TABLES = (
    "weather_forecast_content",
    "weather_forecast_value",
    "weather_forecast_version",
    "weather_forecast_receipt",
)


def _run(conn, monkeypatch, name, step):
    migration = importlib.import_module(f"migrations.versions.{name}")
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
    getattr(migration, step)()


def test_migration_matches_schema_keeps_observations_and_accepts_forecasts(
    migration_engine, monkeypatch, tmp_path
):
    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.services.weather.forecast_archive import ForecastCollector
    from app.services.weather.observation_archive import ObservationCollector

    engine = migration_engine
    metadata.create_all(engine, tables=[car_heater_session])
    with engine.begin() as conn:
        _run(conn, monkeypatch, "20261016_0016_weather_observations", "upgrade")
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    stations = FakeFMI()
    stations.replies[PELMAA] = fmi_reply(PELMAA, {"t2m": every_ten(START, ["-5.0"])})
    stations.replies[AIRPORT] = fmi_reply(AIRPORT, {"vis": every_ten(START, ["5590.0"])})
    ObservationCollector(ctrl, http=stations, clock=lambda: START.timestamp()).run_pending()

    with engine.begin() as conn:
        _run(conn, monkeypatch, "20261016_0017_weather_forecasts", "upgrade")
    inspector = inspect(engine)
    for name in (*FORECAST_TABLES, "weather_retrieval"):
        migrated = {c["name"] for c in inspector.get_columns(name)}
        assert migrated == {c.name for c in metadata.tables[name].c}, name
    nullable = {c["name"]: c["nullable"] for c in inspector.get_columns("weather_retrieval")}
    assert nullable["requested_start"] and nullable["requested_end"]
    assert not nullable["source_id"]
    assert {i["name"] for i in inspector.get_indexes("weather_retrieval")} >= {
        "idx_weather_retrieval_source_retrieved"
    }
    window = ("2027-01-15T00:00:00+00:00", "2027-01-16T00:00:00+00:00")
    # Observation evidence from before the migration is intact.
    assert len(ctrl.list_weather_observations(*window)) == 2
    assert len(ctrl.list_weather_retrievals(*window)) == 2

    fmi = FakeForecastFMI()
    fmi.reply = full_run(3)
    ForecastCollector(ctrl, http=fmi, clock=lambda: START.timestamp()).run_pending()
    [version] = ctrl.list_weather_forecast_versions(*window)
    assert version["value_count"] == 33
    assert ctrl.get_weather_reply(version["receipts"][0]["reply_id"])["content"] == fmi.reply
    ctrl.db.conn.close()

    with engine.begin() as conn:
        _run(conn, monkeypatch, "20261016_0017_weather_forecasts", "downgrade")
    inspector = inspect(engine)
    assert not set(FORECAST_TABLES) & set(inspector.get_table_names())
    with engine.connect() as conn:
        kinds = conn.exec_driver_sql("SELECT kind FROM weather_retrieval").scalars().all()
        # Forecast retrievals are evidence and survive the downgrade.
        assert sorted(kinds) == ["forecast", "observation", "observation"]
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM weather_observation").scalar() == 2
