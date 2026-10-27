"""Reviewed announcement migration and restart recovery on SQLite and PostgreSQL."""

from __future__ import annotations

import contextlib
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from announcement_support import START, FakeCast, FakeSynthesizer, SimulatedCrash, build_service
from pg_fixture import private_postgres as private_postgres
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError

from app.core import Controller
from app.core.schema import (
    announcement_acknowledgement,
    announcement_attempt,
    announcement_fallback,
    announcement_fallback_attempt,
    announcement_occurrence,
    announcement_schedule,
    announcement_snooze,
    announcement_speaker_test,
)

MIGRATIONS = (
    "migrations.versions.20261027_0020_announcement_speaker_tests",
    "migrations.versions.20261027_0021_announcement_schedules",
    "migrations.versions.20261027_0022_announcement_fallbacks",
    "migrations.versions.20261027_0023_announcement_group_evidence",
    "migrations.versions.20261027_0024_announcement_acknowledgements",
    "migrations.versions.20261027_0025_announcement_snooze",
)
TABLES = (
    announcement_speaker_test,
    announcement_attempt,
    announcement_schedule,
    announcement_occurrence,
    announcement_fallback,
    announcement_fallback_attempt,
    announcement_acknowledgement,
    announcement_snooze,
)


def _run(engine, monkeypatch, steps):
    for migration, direction in steps:
        with engine.begin() as conn:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            getattr(migration, direction)()


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'migration.db'}"
    else:
        url = request.getfixturevalue("private_postgres")
    engine = create_engine(url)
    if request.param == "sqlite":  # match the app engine: enforce foreign keys
        event.listen(
            engine, "connect", lambda dbapi, _record: dbapi.execute("PRAGMA foreign_keys=ON")
        )
    migrations = [importlib.import_module(name) for name in MIGRATIONS]
    _run(engine, monkeypatch, [(m, "upgrade") for m in migrations])
    yield engine, migrations
    engine.dispose()


def _shape(engine, table_name):
    inspector = inspect(engine)
    columns = {c["name"]: c["nullable"] for c in inspector.get_columns(table_name)}
    indexes = {i["name"] for i in inspector.get_indexes(table_name)}
    checks = {c["name"] for c in inspector.get_check_constraints(table_name)}
    return columns, indexes, checks


def test_migration_matches_schema_and_enforces_constraints(store, monkeypatch):
    engine, migrations = store
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
    _run(engine, monkeypatch, [(m, "downgrade") for m in reversed(migrations)])
    assert not {t.name for t in TABLES} & set(inspect(engine).get_table_names())
    _run(engine, monkeypatch, [(m, "upgrade") for m in migrations])
    assert {t.name for t in TABLES} <= set(inspect(engine).get_table_names())


SCHEDULE = {
    "id": "s1",
    "kind": "one_time",
    "status": "active",
    "revision": 1,
    "text": "Muista lääkkeet",
    "language": "fi",
    "target_key": "jbl-right",
    "volume_percent": 30,
    "chime_percent": 40,
    "urgent": False,
    "quiet_hours_override": False,
    "expiry_minutes": 15,
    "max_announcements": 1,
    "due_local": "2027-01-15T14:30",
    "due_at": "2027-01-15T12:30:00+00:00",
    "next_due_at": "2027-01-15T12:30:00+00:00",
    "created_at": "2027-01-15T12:00:00+00:00",
    "updated_at": "2027-01-15T12:00:00+00:00",
}
OCCURRENCE = {
    "id": "o1",
    "schedule_id": "s1",
    "instance_due_at": "2027-01-15T12:30:00+00:00",
    "schedule_revision": 1,
    "created_at": "2027-01-15T12:30:00+00:00",
    "text": "Muista lääkkeet",
    "language": "fi",
    "target_key": "jbl-right",
    "target_name": "Jbl-Right",
    "speaker_set": '["jbl-right"]',
    "volume_percent": 30,
    "chime_percent": 40,
    "urgent": False,
    "max_announcements": 1,
    "due_at": "2027-01-15T12:30:00+00:00",
    "expires_at": "2027-01-15T12:45:00+00:00",
    "status": "waiting",
    "waiting_history": "[]",
    "announcements_started": 0,
    "delivery": "none",
    "updated_at": "2027-01-15T12:30:00+00:00",
}
ATTEMPT = {
    "id": "a1",
    "target_key": "jbl-right",
    "target_name": "Jbl-Right",
    "speaker_set": '["jbl-right"]',
    "evidence_scope": "device",
    "priority": 0,
    "text": "Muista lääkkeet",
    "language": "fi",
    "volume_percent": 30,
    "chime_percent": 40,
    "status": "requested",
    "requested_at": "2027-01-15T12:30:00+00:00",
    "dispatch_deadline": "2027-01-15T12:45:00+00:00",
    "updated_at": "2027-01-15T12:30:00+00:00",
}


def _rejected(engine, table, row):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(table.insert().values(**row))


def test_occurrences_are_unique_per_schedule_instance_and_states_are_constrained(store):
    engine, _migrations = store
    with engine.begin() as conn:
        conn.execute(announcement_schedule.insert().values(**SCHEDULE))
        conn.execute(announcement_occurrence.insert().values(**OCCURRENCE))
        conn.execute(announcement_attempt.insert().values(**ATTEMPT, occurrence_id="o1"))
    _rejected(engine, announcement_occurrence, {**OCCURRENCE, "id": "o2"})  # same instance
    _rejected(engine, announcement_schedule, {**SCHEDULE, "id": "s2", "kind": "weekly"})
    _rejected(engine, announcement_schedule, {**SCHEDULE, "id": "s3", "expiry_minutes": 0})
    later = {"instance_due_at": "2027-01-16T12:30:00+00:00"}
    for column, value in (
        ("status", "heard"),
        ("delivery", "acknowledged"),  # acknowledgement is not delivery evidence
        ("disposition", "delivered"),
        ("fallback_reason", "music"),
    ):
        _rejected(
            engine, announcement_occurrence, {**OCCURRENCE, **later, "id": "o3", column: value}
        )
    _rejected(
        engine, announcement_occurrence, {**OCCURRENCE, **later, "id": "o4", "schedule_id": "x"}
    )
    # An attempt belongs to exactly one source: a speaker test or an occurrence.
    _rejected(engine, announcement_attempt, {**ATTEMPT, "id": "a2"})
    with engine.connect() as conn:
        row = (
            conn.execute(
                announcement_occurrence.select().where(announcement_occurrence.c.id == "o1")
            )
            .mappings()
            .one()
        )
    assert (row["retries_started"], row["next_attempt_purpose"]) == (0, None)  # defaults
    ack = {
        "id": "k1",
        "occurrence_id": "o1",
        "action": "done",
        "acknowledged_at": "2027-01-15T12:35:00+00:00",
        "prior_status": "closed",
        "prior_delivery": "delivered",
        "announcements_started": 1,
    }
    with engine.begin() as conn:
        conn.execute(announcement_acknowledgement.insert().values(**ack))
    _rejected(engine, announcement_acknowledgement, {**ack, "id": "k2"})  # one per occurrence
    _rejected(
        engine,
        announcement_acknowledgement,
        {**ack, "id": "k3", "occurrence_id": "o9"},  # unknown occurrence
    )
    later_ack = {**OCCURRENCE, **later, "id": "o5"}
    with engine.begin() as conn:
        conn.execute(announcement_occurrence.insert().values(**later_ack))
    _rejected(
        engine,
        announcement_acknowledgement,
        {**ack, "id": "k4", "occurrence_id": "o5", "action": "dose_taken"},  # not a health record
    )


def test_restart_never_replays_an_uncertain_test_in_either_store(store, tmp_path, monkeypatch):
    from app.core.database import DatabaseManager

    engine, _migrations = store
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


def test_competing_services_create_one_occurrence_and_recover_it_in_either_store(
    store, tmp_path, monkeypatch
):
    from datetime import datetime

    from app.core.database import DatabaseManager

    engine, _migrations = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast = FakeSynthesizer(), FakeCast()
    first = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    second = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    outcome, saved = first.create_schedule(
        {"text": "Muista lääkkeet", "target": "jbl-right", "due_local": "2027-01-15T14:30"},
        request_id="schedule-request-1",
    )
    assert outcome == "created"
    clock[0] = datetime.fromisoformat("2027-01-15T12:30:00+00:00").timestamp()
    # Both read the due schedule before either materializes it.
    due = ctrl.list_due_announcement_schedules("2027-01-15T12:30:00+00:00")
    assert len(due) == 1
    first._materialize_due()
    second._materialize_due()
    cast.play_crash = True
    with contextlib.suppress(SimulatedCrash):
        first.tick()
    cast.play_crash = False
    second.tick()  # sees the occurrence announcing; must not dispatch it again
    assert len(cast.plays) == 1

    clock[0] += 10
    reloaded = Controller(db_path=str(tmp_path / "legacy.db"))
    reloaded._sa_engine = engine
    restarted = build_service(reloaded, clock, (synth, cast), tmp_path / "cache")
    restarted.tick()
    detail = restarted.schedule_detail(saved["schedule"]["id"])
    assert len(detail["occurrences"]) == 1
    occurrence = detail["occurrences"][0]["occurrence"]
    assert occurrence["delivery"] == "unknown"
    assert occurrence["fallback_reason"] == "unknown"
    assert [a["status"] for a in detail["occurrences"][0]["attempts"]] == ["unknown"]
    assert len(cast.plays) == 1
    ctrl.db.conn.close()


def test_recurring_catch_up_is_idempotent_across_services_in_either_store(
    store, tmp_path, monkeypatch
):
    from datetime import datetime

    from app.core.database import DatabaseManager

    engine, _migrations = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast = FakeSynthesizer(), FakeCast()
    first = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    _outcome, saved = first.create_schedule(
        {
            "kind": "recurring",
            "text": "Mittaa verenpaine",
            "target": "jbl-left",
            "recurrence": {"weekdays": [6, 7], "times": ["20:00", "08:00"]},
        },
        request_id="recurring-request-1",
    )
    schedule_id = saved["schedule"]["id"]
    # Down from Friday 14:00 until Sunday 08:05 Helsinki; two services resume.
    clock[0] = datetime.fromisoformat("2027-01-17T06:05:00+00:00").timestamp()
    second = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    third = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    second.tick()
    third.tick()
    second.tick()
    detail = third.schedule_detail(schedule_id)
    assert detail["schedule"]["recurrence"] == {"weekdays": [6, 7], "times": ["08:00", "20:00"]}
    assert detail["schedule"]["next_due_at"] == "2027-01-17T18:00:00+00:00"
    occurrences = sorted(
        (item["occurrence"] for item in detail["occurrences"]), key=lambda o: o["due_at"]
    )
    assert [(o["instance_due_at"], o["disposition"]) for o in occurrences] == [
        ("2027-01-16T06:00:00+00:00", "missed"),
        ("2027-01-16T18:00:00+00:00", "missed"),
        ("2027-01-17T06:00:00+00:00", None),
    ]
    assert len(cast.plays) == 1
    ctrl.db.conn.close()


FALLBACK = {
    "id": "f1",
    "occurrence_id": "o1",
    "channel": "discord",
    "reason": "failed",
    "message": "Muista lääkkeet",
    "status": "pending",
    "attempts_made": 0,
    "max_attempts": 3,
    "created_at": "2027-01-15T12:30:00+00:00",
    "updated_at": "2027-01-15T12:30:00+00:00",
}


def test_one_fallback_per_occurrence_and_numbered_attempts_are_constrained(store):
    engine, _migrations = store
    with engine.begin() as conn:
        conn.execute(announcement_schedule.insert().values(**SCHEDULE))
        conn.execute(announcement_occurrence.insert().values(**OCCURRENCE))
        conn.execute(announcement_fallback.insert().values(**FALLBACK))
        conn.execute(
            announcement_fallback_attempt.insert().values(
                id="fa1",
                fallback_id="f1",
                number=1,
                status="sending",
                intent_at="2027-01-15T12:30:00+00:00",
            )
        )
    _rejected(engine, announcement_fallback, {**FALLBACK, "id": "f2"})  # same occurrence
    for column, value in (
        ("status", "read"),  # Discord acceptance is not readership
        ("reason", "unacknowledged"),  # lack of acknowledgement is not a trigger
        ("attempts_made", 4),
        ("occurrence_id", "missing"),
    ):
        _rejected(engine, announcement_fallback, {**FALLBACK, "id": "f3", column: value})
    attempt = {
        "id": "fa2",
        "fallback_id": "f1",
        "number": 1,
        "status": "accepted",
        "intent_at": "2027-01-15T12:31:00+00:00",
    }
    _rejected(engine, announcement_fallback_attempt, attempt)  # number 1 already used
    _rejected(engine, announcement_fallback_attempt, {**attempt, "number": 2, "status": "read"})


def test_competing_services_send_one_fallback_request_in_either_store(store, tmp_path, monkeypatch):
    from datetime import datetime

    from announcement_support import FakeDiscord

    from app.core.database import DatabaseManager

    engine, _migrations = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast, discord = FakeSynthesizer(), FakeCast(), FakeDiscord()
    first = build_service(ctrl, clock, (synth, cast), tmp_path / "cache", discord=discord)
    second = build_service(ctrl, clock, (synth, cast), tmp_path / "cache", discord=discord)
    _outcome, saved = first.create_schedule(
        {"text": "Muista lääkkeet", "target": "jbl-right", "due_local": "2027-01-15T14:30"}
    )
    synth.failure = "synthesis_failed"
    clock[0] = datetime.fromisoformat("2027-01-15T12:30:00+00:00").timestamp()
    first._materialize_due()
    first._dispatch_work()
    # Both see the eligible occurrence and its pending fallback before either sends.
    (eligible,) = ctrl.list_announcement_occurrences_without_fallback()
    first._open_fallback(eligible, clock[0])
    second._open_fallback(eligible, clock[0])
    (pending,) = ctrl.list_announcement_fallbacks(statuses=("pending",))
    first._send_fallback(pending)
    second._send_fallback(pending)
    second.tick()
    first.tick()
    assert len(discord.requests) == 1

    reloaded = Controller(db_path=str(tmp_path / "legacy.db"))
    reloaded._sa_engine = engine
    restarted = build_service(reloaded, clock, (synth, cast), tmp_path / "cache", discord=discord)
    clock[0] += 3600
    restarted.tick()
    (item,) = restarted.schedule_detail(saved["schedule"]["id"])["occurrences"]
    assert item["fallback"]["status"] == "accepted"
    assert [a["number"] for a in item["fallback"]["attempts"]] == [1]
    assert len(discord.requests) == 1
    ctrl.db.conn.close()


def test_acknowledgements_are_once_per_occurrence_and_survive_restart_in_either_store(
    store, tmp_path, monkeypatch
):
    from datetime import datetime

    from app.core.database import DatabaseManager
    from app.services.announcements import AnnouncementStateError

    engine, _migrations = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast = FakeSynthesizer(), FakeCast()
    first = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    second = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    _outcome, saved = first.create_schedule(
        {
            "text": "Muista lääkkeet",
            "target": "jbl-right",
            "due_local": "2027-01-15T14:30",
            "max_announcements": 2,
            "repeat_interval_minutes": 5,
        }
    )
    clock[0] = datetime.fromisoformat("2027-01-15T12:30:00+00:00").timestamp()
    occurrence_ids = []

    def acknowledge_while_preparing():
        (item,) = first.schedule_detail(saved["schedule"]["id"])["occurrences"]
        occurrence_ids.append(item["occurrence"]["id"])
        first.acknowledge_occurrence(item["occurrence"]["id"], "done", by="owner")

    synth.during = acknowledge_while_preparing
    second.tick()  # read the occurrence as waiting, then Done committed first
    second.tick()
    assert cast.plays == []
    (occurrence_id,) = occurrence_ids
    repeated = second.acknowledge_occurrence(occurrence_id, "done", by="owner")
    with pytest.raises(AnnouncementStateError) as conflict:
        second.acknowledge_occurrence(occurrence_id, "skipped", by="owner")
    assert conflict.value.code == "already_acknowledged"

    clock[0] += 600
    reloaded = Controller(db_path=str(tmp_path / "legacy.db"))
    reloaded._sa_engine = engine
    restarted = build_service(reloaded, clock, (synth, cast), tmp_path / "cache")
    restarted.tick()
    detail = restarted.occurrence_detail(occurrence_id)
    assert detail["acknowledgement"] == repeated["acknowledgement"]
    assert detail["acknowledgement"]["prior_status"] == "waiting"
    assert detail["occurrence"]["disposition"] == "done"
    assert detail["occurrence"]["fallback_reason"] is None
    assert detail["attempts"] == []
    assert cast.plays == []
    ctrl.db.conn.close()


def test_snooze_is_durable_idempotent_and_superseded_in_either_store(store, tmp_path, monkeypatch):
    from datetime import datetime

    from app.core.database import DatabaseManager
    from app.services.announcements import AnnouncementStateError

    engine, _migrations = store
    monkeypatch.setattr(DatabaseManager, "_instance", None)
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    clock = [START]
    synth, cast = FakeSynthesizer(), FakeCast()
    first = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    second = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    _, saved = first.create_schedule(
        {
            "text": "Muista lääkkeet",
            "target": "jbl-right",
            "kind": "recurring",
            "recurrence": {"weekdays": [5], "times": ["14:30", "14:35"]},
        }
    )
    clock[0] = datetime.fromisoformat("2027-01-15T12:30:00+00:00").timestamp()

    def postpone():
        (item,) = first.schedule_detail(saved["schedule"]["id"])["occurrences"]
        first.snooze_occurrence(
            item["occurrence"]["id"], request_id="durable-snooze-request", minutes=30
        )

    synth.during = postpone
    second.tick()
    (item,) = first.schedule_detail(saved["schedule"]["id"])["occurrences"]
    oid = item["occurrence"]["id"]
    assert cast.plays == []
    restarted = build_service(ctrl, clock, (synth, cast), tmp_path / "cache")
    again = restarted.snooze_occurrence(oid, request_id="durable-snooze-request", minutes=30)
    assert again["snoozes"] == item["snoozes"]
    assert again["occurrence"]["announcements_started"] == 0
    clock[0] += 300
    restarted.tick()
    second.tick()
    old = restarted.occurrence_detail(oid)
    assert old["occurrence"]["disposition"] == "superseded"
    assert old["acknowledgement"] is None
    assert old["snoozes"] == item["snoozes"]
    assert len(cast.plays) == 1
    with pytest.raises(AnnouncementStateError):
        restarted.snooze_occurrence(oid, request_id="revive-snooze-request", minutes=30)
    ctrl.db.conn.close()
