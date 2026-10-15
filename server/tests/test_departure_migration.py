"""Reviewed departure migration on isolated SQLite and PostgreSQL databases."""

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres

from app.core.schema import car_heater_departure_plan, car_heater_session, metadata


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


def test_migration_preserves_sessions_and_enforces_one_current_revision(
    migration_engine, monkeypatch
):
    engine = migration_engine
    metadata.create_all(engine, tables=[car_heater_session])
    with engine.begin() as conn:
        for number in range(3):
            conn.execute(
                car_heater_session.insert().values(
                    id=str(number),
                    trigger="defrost",
                    created_at="2027-01-15T12:00:00+00:00",
                    observed_at="2027-01-15T12:00:00+00:00",
                    snapshot="{}",
                )
            )
    migration = importlib.import_module("migrations.versions.20261015_0014_car_heater_departure")
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        conn.execute(
            car_heater_departure_plan.insert().values(
                session_id="0", is_current=False, payload="{}"
            )
        )
        conn.execute(
            car_heater_departure_plan.insert().values(session_id="1", is_current=True, payload="{}")
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            car_heater_departure_plan.insert().values(session_id="2", is_current=True, payload="{}")
        )
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.downgrade()
        assert len(conn.execute(car_heater_session.select()).all()) == 3
        migration.upgrade()
    assert "car_heater_departure_plan" in inspect(engine).get_table_names()
