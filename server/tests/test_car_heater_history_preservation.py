"""Permanent heater history through migration, restart and recovery seams."""

import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Blueprint, Flask
from flask_login import LoginManager, UserMixin
from sqlalchemy import create_engine, text

from app.blueprints.api.car_heater import control
from app.core import Controller
from app.core.database import DatabaseManager
from app.core.models import CarHeaterStatus
from app.core.schema import metadata

OLD_DAY = "2020-01-15"
OLD_TRIGGER = """
CREATE TRIGGER cleanup_car_heater_status_after_insert
AFTER INSERT ON car_heater_status BEGIN
DELETE FROM car_heater_status WHERE timestamp < datetime('now', '-30 days');
END
"""


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    path = tmp_path / "history.db"
    ctrl = Controller(db_path=str(path))
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    ctrl._sa_engine = engine
    yield path, ctrl, engine
    ctrl.db.conn.close()
    engine.dispose()


def history_client(ctrl):
    class Owner(UserMixin):
        id = "owner"

    app = Flask(__name__)
    app.config.update(SECRET_KEY="isolated-test", TESTING=True)
    app.ctrl = ctrl
    login = LoginManager(app)
    login.user_loader(lambda _id: Owner())
    root = Blueprint("api", __name__, url_prefix="/api")
    root.register_blueprint(control.car_bp)
    app.register_blueprint(root)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "owner"
        session["_fresh"] = True
    return client


def record(ctrl, timestamp, power=1200):
    return ctrl.record_car_heater_status(
        CarHeaterStatus(
            id=None,
            timestamp=timestamp,
            is_heater_on=True,
            instant_power_w=power,
            voltage_v=None,
            current_a=None,
            energy_total_wh=None,
            energy_last_min_wh=None,
            energy_ts=None,
            device_temp_c=None,
            device_temp_f=None,
            ambient_temp=None,
            source="test",
        )
    )


def assert_old_history(ctrl):
    client = history_client(ctrl)
    response = client.get(f"/api/car_heater/history?date={OLD_DAY}")
    assert response.status_code == 200
    assert [(row["timestamp"], row["instant_power_w"]) for row in response.json["rows"]] == [
        (f"{OLD_DAY}T08:00:00+02:00", 1200)
    ]
    with client.session_transaction() as session:
        session.clear()
    assert client.get(f"/api/car_heater/history?date={OLD_DAY}").status_code == 401


def assert_history_after_restart(path, database_url):
    import subprocess
    import sys

    # A fresh interpreter exercises real bootstrap and reopened DB connections.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from runpy import run_path; "
            "from sqlalchemy import create_engine; from app.core import Controller; "
            "ctrl = Controller(db_path=sys.argv[1]); "
            "ctrl._sa_engine = create_engine(sys.argv[2]); "
            "run_path('tests/test_car_heater_history_preservation.py')['assert_old_history'](ctrl)",
            str(path),
            database_url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_bootstrap_preserves_old_history_after_telemetry_and_restart(database):
    path, ctrl, _engine = database
    record(ctrl, f"{OLD_DAY}T08:00:00+02:00")
    record(ctrl, datetime.now(UTC).isoformat())
    assert_history_after_restart(path, f"sqlite:///{path}")
    assert_old_history(ctrl)


def test_upgrade_preserves_history_and_household_retention(database, monkeypatch):
    path, ctrl, engine = database
    record(ctrl, f"{OLD_DAY}T08:00:00+02:00")
    with engine.begin() as conn:
        conn.exec_driver_sql(OLD_TRIGGER)
    migration = importlib.import_module(
        "migrations.versions.20261014_0011_permanent_heater_history"
    )
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        # Repeat-safe removal; unrelated sensor cleanup still removes expired rows.
        migration.upgrade()
        conn.execute(
            text(
                "INSERT INTO esp32_temphum(location,timestamp,temperature,humidity) "
                "VALUES ('household','2020-01-15',20,40)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO esp32_temphum(location,timestamp,temperature,humidity) "
                "VALUES ('household',datetime('now'),21,41)"
            )
        )
        assert conn.scalar(text("SELECT count(*) FROM esp32_temphum")) == 1
    record(ctrl, datetime.now(UTC).isoformat())
    assert_history_after_restart(path, f"sqlite:///{path}")
    assert_old_history(ctrl)


def test_read_only_discovery_and_backup_restore_through_history(database, tmp_path):
    path, ctrl, engine = database
    record(ctrl, f"{OLD_DAY}T08:00:00+02:00")
    before = path.read_bytes()
    discovery = subprocess.run(
        [sys.executable, "scripts/heater_history.py", "discover"],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{path}"},
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(discovery.stdout)
    assert path.read_bytes() == before
    assert report["tables"]["car_heater_status"] == {
        "count": 1,
        "oldest": f"{OLD_DAY}T08:00:00+02:00",
        "newest": f"{OLD_DAY}T08:00:00+02:00",
    }
    assert "cleanup_esp32_temphum_after_insert" in str(report["triggers"])
    assert "cleanup_car_heater_status_after_insert" not in str(report["triggers"])
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    for source, destination in [(path, backup), (backup, restored)]:
        subprocess.run(
            [
                sys.executable,
                "scripts/heater_history.py",
                "sqlite-copy",
                str(source),
                str(destination),
            ],
            check=True,
            capture_output=True,
        )
    refused = subprocess.run(
        [sys.executable, "scripts/heater_history.py", "sqlite-copy", str(path), str(backup)],
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 1
    assert "FileExistsError" in refused.stderr
    assert backup.stat().st_mode & 0o777 == 0o600
    ctrl._sa_engine = create_engine(f"sqlite:///{restored}")
    try:
        record(ctrl, datetime.now(UTC).isoformat())
        assert_history_after_restart(restored, f"sqlite:///{restored}")
        assert_old_history(ctrl)
    finally:
        ctrl._sa_engine.dispose()
        ctrl._sa_engine = engine


@pytest.fixture
def isolated_postgres(tmp_path):
    """Private Unix-socket cluster: no network listener or deployed connection."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    binaries = Path("/usr/lib/postgresql/15/bin")
    if not (binaries / "initdb").exists():
        pytest.skip("PostgreSQL 15 test binaries are unavailable")
    cluster = tmp_path / "pg"
    socket = Path(tempfile.mkdtemp(prefix="heater-pg-"))
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
            f"-k {socket} -p 55439 -c listen_addresses=''",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield binaries, socket
    finally:
        subprocess.run(
            [str(binaries / "pg_ctl"), "-D", str(cluster), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
        )
        shutil.rmtree(socket)


def test_postgresql_migration_discovery_and_dump_restore(
    database, isolated_postgres, tmp_path, monkeypatch
):
    import subprocess

    from scripts.heater_history import discover
    from sqlalchemy.engine import URL

    binaries, socket = isolated_postgres
    legacy_path, ctrl, sqlite_engine = database
    url = URL.create(
        "postgresql+psycopg", database="postgres", query={"host": str(socket), "port": "55439"}
    )
    engine = create_engine(url)
    metadata.create_all(
        engine,
        tables=[
            metadata.tables[name]
            for name in ("car_heater_status", "car_heater_sensor_frame", "esp32_temphum")
        ],
    )
    ctrl._sa_engine = engine
    try:
        record(ctrl, f"{OLD_DAY}T08:00:00+02:00")
        with engine.begin() as conn:
            conn.exec_driver_sql("""CREATE FUNCTION heater_cleanup() RETURNS trigger LANGUAGE plpgsql
                AS $$ BEGIN DELETE FROM car_heater_status WHERE timestamp < '2026-01-01';
                RETURN NEW; END $$""")
            conn.exec_driver_sql("""CREATE TRIGGER cleanup_car_heater_status_after_insert
                AFTER INSERT ON car_heater_status FOR EACH ROW EXECUTE FUNCTION heater_cleanup()""")
            conn.exec_driver_sql("""CREATE FUNCTION household_cleanup() RETURNS trigger LANGUAGE plpgsql
                AS $$ BEGIN DELETE FROM esp32_temphum WHERE timestamp < '2026-01-01';
                RETURN NEW; END $$""")
            conn.exec_driver_sql("""CREATE TRIGGER cleanup_esp32_temphum_after_insert
                AFTER INSERT ON esp32_temphum FOR EACH ROW EXECUTE FUNCTION household_cleanup()""")
        report = discover(url.render_as_string(hide_password=False))
        assert report["tables"]["car_heater_status"]["count"] == 1
        assert "DELETE FROM car_heater_status" in str(report["triggers"])
        migration = importlib.import_module(
            "migrations.versions.20261014_0011_permanent_heater_history"
        )
        with engine.begin() as conn:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()
            conn.exec_driver_sql(
                "INSERT INTO esp32_temphum(location,timestamp,temperature,humidity) "
                "VALUES ('household','2020-01-15',20,40)"
            )
            conn.exec_driver_sql(
                "INSERT INTO esp32_temphum(location,timestamp,temperature,humidity) "
                "VALUES ('household','2026-10-14',21,41)"
            )
            assert conn.scalar(text("SELECT count(*) FROM esp32_temphum")) == 1
        record(ctrl, datetime.now(UTC).isoformat())
        engine.dispose()
        assert_history_after_restart(legacy_path, url.render_as_string(hide_password=False))
        assert_old_history(ctrl)
        archive = tmp_path / "history.dump"
        subprocess.run(
            [
                str(binaries / "pg_dump"),
                "-h",
                str(socket),
                "-p",
                "55439",
                "-d",
                "postgres",
                "-Fc",
                "--no-owner",
                "--no-acl",
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
                "-h",
                str(socket),
                "-p",
                "55439",
                "-d",
                "restored",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                str(archive),
            ],
            check=True,
            capture_output=True,
        )
        restored = create_engine(url.set(database="restored"))
        ctrl._sa_engine = restored
        try:
            record(ctrl, datetime.now(UTC).isoformat())
            assert_history_after_restart(
                legacy_path, url.set(database="restored").render_as_string(hide_password=False)
            )
            assert_old_history(ctrl)
        finally:
            restored.dispose()
    finally:
        ctrl._sa_engine = sqlite_engine
        engine.dispose()
