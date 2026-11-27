"""Outbox migration and delivery claims against isolated SQLite/PostgreSQL."""

import importlib
import subprocess
import tempfile
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL

from app.core._controller.car_heater_notifications import CarHeaterNotificationMixin
from app.core.schema import car_heater_notification


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'notices.db'}")
        yield engine
        engine.dispose()
        return
    installed = list(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
    if not installed:
        pytest.skip("PostgreSQL binaries unavailable")
    binaries = max(installed, key=lambda p: int(p.parent.parent.name)).parent
    cluster = tmp_path / "pg"
    with tempfile.TemporaryDirectory(prefix="notify-pg-") as socket:
        subprocess.run(
            [str(binaries / "initdb"), "-D", str(cluster), "-A", "trust"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                str(binaries / "pg_ctl"),
                "-D",
                str(cluster),
                "-l",
                str(tmp_path / "pg.log"),
                "-o",
                f"-k {socket} -p 55438 -c listen_addresses=''",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        engine = create_engine(
            URL.create(
                "postgresql+psycopg", database="postgres", query={"host": socket, "port": "55438"}
            )
        )
        try:
            yield engine
        finally:
            engine.dispose()
            subprocess.run(
                [str(binaries / "pg_ctl"), "-D", str(cluster), "-m", "immediate", "-w", "stop"],
                check=True,
                capture_output=True,
            )


def test_notification_migration_and_persistent_claims(migration_engine, monkeypatch):
    engine = migration_engine
    migration = importlib.import_module(
        "migrations.versions.20261119_0028_car_heater_notifications"
    )
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
    schema = inspect(engine)
    assert {c["name"] for c in schema.get_columns("car_heater_notification")} == set(
        car_heater_notification.c.keys()
    )
    assert schema.get_indexes("car_heater_notification")[0]["column_names"] == [
        "status",
        "next_attempt_at",
    ]
    ctrl = CarHeaterNotificationMixin()
    ctrl._sa_engine = engine
    now = "2026-11-19T07:14:49+00:00"
    assert ctrl.queue_heater_notification("session:restart", "Interrupted", now)
    assert not ctrl.queue_heater_notification("session:restart", "Duplicate", now)
    claimed = ctrl.claim_heater_notification(now)
    assert claimed["attempts"] == 1
    assert ctrl.claim_heater_notification(now) is None
    ctrl.recover_heater_notifications(now)
    assert ctrl.claim_heater_notification(now) is None
    with engine.begin() as conn:
        assert (
            conn.execute(car_heater_notification.select()).mappings().one()["status"] == "unknown"
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.downgrade()
    assert "car_heater_notification" not in inspect(engine).get_table_names()
