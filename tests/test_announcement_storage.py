"""Reviewed announcement migration and restart recovery on SQLite and PostgreSQL."""

from __future__ import annotations

import contextlib
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from announcement_support import START, FakeCast, FakeSynthesizer, SimulatedCrash, build_service
from pg_fixture import private_postgres as private_postgres
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.core import Controller
from app.core.schema import announcement_attempt, announcement_speaker_test

MIGRATION = "migrations.versions.20261027_0020_announcement_speaker_tests"
TABLES = (announcement_speaker_test, announcement_attempt)


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'migration.db'}"
    else:
        url = request.getfixturevalue("private_postgres")
    engine = create_engine(url)
    migration = importlib.import_module(MIGRATION)
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
    yield engine, migration
    engine.dispose()


def _shape(engine, table_name):
    inspector = inspect(engine)
    columns = {c["name"]: c["nullable"] for c in inspector.get_columns(table_name)}
    indexes = {i["name"] for i in inspector.get_indexes(table_name)}
    checks = {c["name"] for c in inspector.get_check_constraints(table_name)}
    return columns, indexes, checks


def test_migration_matches_schema_and_enforces_constraints(store, monkeypatch):
    engine, migration = store
    for table in TABLES:
        columns, indexes, checks = _shape(engine, table.name)
        assert columns == {c.name: c.nullable for c in table.columns}
        assert {i.name for i in table.indexes} <= indexes
        expected_checks = {c.name for c in table.constraints if c.name and c.name.startswith("ck_")}
        assert expected_checks <= checks
    test_row = {
        "id": "t1",
        "request_id": "request-0001",
        "requested_at": "2027-01-15T12:00:00+00:00",
        "text": "Hei",
        "language": "fi",
        "target_key": "jbl-right",
        "volume_percent": 30,
        "chime_percent": 40,
    }
    with engine.begin() as conn:
        conn.execute(announcement_speaker_test.insert().values(**test_row))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(announcement_speaker_test.insert().values(**{**test_row, "id": "t2"}))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            announcement_speaker_test.insert().values(
                **{**test_row, "id": "t3", "request_id": "request-0003", "language": "sv"}
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            announcement_attempt.insert().values(
                id="a1",
                speaker_test_id="t1",
                target_key="jbl-right",
                target_name="Jbl-Right",
                speaker_set='["jbl-right"]',
                evidence_scope="device",
                priority=0,
                text="Hei",
                language="fi",
                volume_percent=30,
                chime_percent=40,
                status="heard",  # not a status the receiver can establish
                requested_at="2027-01-15T12:00:00+00:00",
                dispatch_deadline="2027-01-15T12:02:00+00:00",
                updated_at="2027-01-15T12:00:00+00:00",
            )
        )
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.downgrade()
    assert not {t.name for t in TABLES} & set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
    assert {t.name for t in TABLES} <= set(inspect(engine).get_table_names())


def test_restart_never_replays_an_uncertain_test_in_either_store(store, tmp_path, monkeypatch):
    from app.core.database import DatabaseManager

    engine, _migration = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast = FakeSynthesizer(), FakeCast()
    service = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    outcome, first = service.request_speaker_test(
        text="Muista lääkkeet", target="jbl-right", request_id="browser-request-1"
    )
    again, repeated = service.request_speaker_test(
        text="Muista lääkkeet", target="jbl-right", request_id="browser-request-1"
    )
    assert (outcome, again) == ("created", "exists")
    assert repeated["test"]["id"] == first["test"]["id"]
    cast.play_crash = True
    with contextlib.suppress(SimulatedCrash):
        service.tick()
    cast.play_crash = False

    clock[0] += 10
    reloaded = Controller(db_path=str(tmp_path / "legacy.db"))
    reloaded._sa_engine = engine
    restarted = build_service(reloaded, clock, (synth, cast), tmp_path / "cache")
    restarted.tick()
    attempt = restarted.speaker_test_detail(first["test"]["id"])["attempt"]
    assert attempt["status"] == "unknown"
    assert attempt["reason"] == "interrupted_by_restart"
    assert len(cast.plays) == 1
    ctrl.db.conn.close()
