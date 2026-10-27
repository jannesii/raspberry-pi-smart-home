"""Actual isolated backup/restore through fresh public service and Controller."""

import sqlite3
import subprocess

import pytest
import test_announcement_storage as storage_support
from announcement_support import (
    START,
    DiscordResponse,
    FakeCast,
    FakeDiscord,
    FakeSynthesizer,
    build_service,
)
from pg_fixture import _binaries, private_postgres as private_postgres
from sqlalchemy import create_engine
from test_announcement_acknowledgements import RIGHT, at, play_through

store = storage_support.store

from app.core import Controller


def controller(engine, tmp_path):
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = engine
    return ctrl


def restore(engine, tmp_path):
    if engine.dialect.name == "sqlite":
        target = tmp_path / "restored.db"
        with sqlite3.connect(engine.url.database) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return create_engine(f"sqlite:///{target}")
    binaries = _binaries()
    args = ["-h", engine.url.query["host"], "-p", engine.url.query["port"]]
    archive = tmp_path / "announcements.dump"
    subprocess.run(
        [
            str(binaries / "pg_dump"),
            *args,
            "-d",
            engine.url.database,
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
        conn.exec_driver_sql("CREATE DATABASE history_restored")
    subprocess.run(
        [
            str(binaries / "pg_restore"),
            *args,
            "-d",
            "history_restored",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    return create_engine(engine.url.set(database="history_restored"))


def test_history_actions_snapshots_channels_and_cache_survive_backup_restore(store, tmp_path):
    engine, _ = store
    ctrl = controller(engine, tmp_path)
    clock = [START]
    doubles = FakeSynthesizer(), FakeCast()
    discord = FakeDiscord()
    service = build_service(ctrl, clock, doubles, tmp_path / "source-cache", discord=discord)
    payload = {
        "text": "Private original Finnish text",
        "language": "fi",
        "target": "jbl-right",
        "due_local": "2027-01-15T14:30",
        "volume_percent": 25,
        "chime_percent": 20,
    }
    _, saved = service.create_schedule(payload)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    identity = service.list_occurrences()[0]["occurrence"]["id"]
    service.acknowledge_occurrence(identity, "done", by="owner")
    original = service.occurrence_detail(identity)
    assert original["occurrence"]["delivery"] == "delivered"
    assert original["attempts"][0]["volume_restore"] == "restored"
    assert original["acknowledgement"]["action"] == "done"
    assert original["attempts"][0]["intent_at"] == "2027-01-15T12:30:00+00:00"
    assert original["attempts"][0]["playback_observed_at"] == "2027-01-15T12:30:01+00:00"
    service.update_schedule(
        sid, {**payload, "text": "Changed future text", "due_local": "2027-01-15T15:30"}
    )
    # A recurring schedule keeps its future cursor across restoration.
    _, recurring = service.create_schedule(
        {
            **payload,
            "text": "Recurring retained text",
            "kind": "recurring",
            "recurrence": {"weekdays": [5], "times": ["14:40", "14:45"]},
        }
    )
    at(clock, "2027-01-15T12:40:00+00:00")
    doubles[0].failure = "voice_unavailable"
    discord.outcomes = [DiscordResponse(429, {"retry_after": 300}), DiscordResponse(204)]
    service.tick()
    failed = next(
        item
        for item in service.list_occurrences()
        if item["occurrence"]["schedule_id"] == recurring["schedule"]["id"]
    )
    assert failed["fallback"]["status"] == "pending"
    assert failed["fallback"]["attempts_made"] == 1
    cursor = service.schedule_detail(recurring["schedule"]["id"])["schedule"]["next_due_at"]
    # Snooze survives independently of delivery and acknowledgement.
    _, pending = service.create_schedule({**payload, "due_local": "2027-01-15T14:41"})
    doubles[0].failure = None
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:41:00+00:00")
    service.tick()
    active = next(
        item
        for item in service.list_occurrences()
        if item["occurrence"]["schedule_id"] == pending["schedule"]["id"]
    )
    service.snooze_occurrence(
        active["occurrence"]["id"], minutes=10, request_id="restore-snooze-request", by="owner"
    )
    snoozed = service.occurrence_detail(active["occurrence"]["id"])
    restored_engine = restore(engine, tmp_path)
    try:
        fresh_ctrl = controller(restored_engine, tmp_path)
        fresh_doubles = FakeSynthesizer(), FakeCast()
        fresh_discord = FakeDiscord()
        fresh_discord.outcomes = [DiscordResponse(204)]
        fresh = build_service(
            fresh_ctrl, clock, fresh_doubles, tmp_path / "empty-cache", discord=fresh_discord
        )
        assert fresh.occurrence_detail(identity) == original
        assert fresh.occurrence_detail(active["occurrence"]["id"]) == snoozed
        assert (
            fresh.schedule_detail(recurring["schedule"]["id"])["schedule"]["next_due_at"] == cursor
        )
        assert fresh.occurrence_detail(failed["occurrence"]["id"])["fallback"] == failed["fallback"]
        assert fresh_doubles[0].calls == []
        fresh.tick()
        assert fresh_doubles[1].plays == []
        assert len(fresh_discord.requests) == 0
        at(clock, "2027-01-15T12:45:00+00:00")
        fresh.tick()
        retry = fresh.occurrence_detail(failed["occurrence"]["id"])["fallback"]
        assert retry["attempts_made"] == 2
        assert retry["status"] == "accepted"
        fresh.tick()
        assert len(fresh_discord.requests) == 1
        # Future eligible work regenerates from retained text with empty cache.
        assert len(fresh_doubles[1].plays) == 1
        assert fresh_doubles[0].calls
        assert fresh.occurrence_detail(identity)["occurrence"]["text"] == payload["text"]
    finally:
        restored_engine.dispose()


def test_deleted_instance_cannot_reappear_after_cursor_rewind_and_restart(store, tmp_path):
    engine, _ = store
    ctrl = controller(engine, tmp_path)
    clock = [START]
    doubles = FakeSynthesizer(), FakeCast()
    service = build_service(ctrl, clock, doubles, tmp_path / "cache")
    _, detail = service.create_schedule(
        {"text": "Erased private text", "target": "jbl-right", "due_local": "2027-01-15T14:30"}
    )
    sid = detail["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    item = service.list_occurrences()[0]
    identity = item["occurrence"]["id"]
    service.delete_history("occurrences", identity)
    assert service.occurrence_detail(identity) is None
    # Controller is the durable public seam: even a restored/backdated cursor
    # cannot recreate the deleted identity and reset its announcement budget.
    ctrl.update_announcement_schedule(sid, next_due_at="2027-01-15T12:30:00+00:00")
    fresh = build_service(controller(engine, tmp_path), clock, doubles, tmp_path / "fresh")
    fresh.tick()
    assert fresh.list_occurrences() == []
    assert fresh.schedule_detail(sid)["schedule"]["next_due_at"] is None
    assert len(doubles[1].plays) == 1


def test_unopened_fallback_remains_eligible_until_resolved_before_deletion(store, tmp_path):
    from app.services.announcements import AnnouncementStateError

    engine, _ = store
    ctrl = controller(engine, tmp_path)
    clock = [START]
    doubles = FakeSynthesizer(), FakeCast()
    service = build_service(ctrl, clock, doubles, tmp_path / "cache")
    _, _ = service.create_schedule(
        {"text": "Awaiting fallback", "target": "jbl-right", "due_local": "2027-01-15T14:30"}
    )
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    identity = service.list_occurrences()[0]["occurrence"]["id"]
    # Persist the eligible state that can survive a crash before fallback opens.
    ctrl.update_announcement_occurrence(
        identity, fallback_reason="failed", fallback_eligible_at="2027-01-15T12:30:05+00:00"
    )
    with pytest.raises(AnnouncementStateError, match="pending"):
        service.delete_history("occurrences", identity)
    service.tick()
    assert service.occurrence_detail(identity)["fallback"]["status"] == "unavailable"
    service.delete_history("occurrences", identity)
    assert service.occurrence_detail(identity) is None


def test_restoration_evidence_and_unsupported_restart_do_not_resume_music(store, tmp_path):
    from cryptography.fernet import Fernet
    from test_announcement_spotify import SpotifyServer

    from app.services.announcements.spotify import SpotifyHTTP, SpotifyRestoration

    engine, _ = store
    ctrl = controller(engine, tmp_path)
    clock = [START]
    doubles = FakeSynthesizer(), FakeCast()
    service = build_service(ctrl, clock, doubles, tmp_path / "cache")
    server = SpotifyServer()
    key = Fernet.generate_key()

    def spotify(ctrl):
        return SpotifyRestoration(
            ctrl,
            key=key,
            clock=lambda: clock[0],
            http=SpotifyHTTP(
                "client",
                "secret",
                "https://home.test/api/announcements/spotify/callback",
                request=server.request,
            ),
        )

    service.spotify = spotify(ctrl)
    service.spotify.connect("code-secret")
    service.spotify.configure(True, {"jbl-right": "spotify-right"}, service.config.targets)
    service.create_schedule(
        {
            "text": "Restore music",
            "target": "jbl-right",
            "urgent": True,
            "due_local": "2027-01-15T14:30",
        }
    )
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    item = service.list_occurrences()[0]
    assert item["attempts"][0]["media_restore"] == "restored"
    assert item["occurrence"]["delivery"] == "delivered"
    assert item["acknowledgement"] is None
    # Keep a second urgent interruption in flight when the database is backed up.
    service.create_schedule(
        {
            "text": "Uncertain in flight",
            "target": "jbl-right",
            "urgent": True,
            "due_local": "2027-01-15T14:31",
        }
    )
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    pending = next(
        i for i in service.list_occurrences() if i["occurrence"]["text"] == "Uncertain in flight"
    )
    assert pending["attempts"][0]["media_restore"] == "pending"
    restored_engine = restore(engine, tmp_path)
    try:
        fresh_ctrl = controller(restored_engine, tmp_path)
        fresh = build_service(
            fresh_ctrl, clock, (FakeSynthesizer(), FakeCast()), tmp_path / "empty-cache"
        )
        fresh.spotify = spotify(fresh_ctrl)
        before = len([c for c in server.calls if c[1].endswith("/play")])
        fresh.recover()
        fresh.tick()
        assert fresh.occurrence_detail(item["occurrence"]["id"]) == item
        unknown = fresh.occurrence_detail(pending["occurrence"]["id"])
        assert unknown["attempts"][0]["media_restore"] == "unknown"
        assert unknown["occurrence"]["delivery"] == "unknown"
        assert unknown["acknowledgement"] is None
        assert len([c for c in server.calls if c[1].endswith("/play")]) == before
    finally:
        restored_engine.dispose()


def test_restart_resolves_terminal_attempt_restoration_gap_without_replaying(store, tmp_path):
    from app.services.announcements import AnnouncementStateError

    engine, _ = store
    ctrl = controller(engine, tmp_path)
    clock = [START]
    doubles = FakeSynthesizer(), FakeCast()
    service = build_service(ctrl, clock, doubles, tmp_path / "cache")
    _, detail = service.request_speaker_test(text="Crash after playback", target="jbl-right")
    identity = detail["test"]["id"]
    service.tick()
    attempt_id = service.speaker_test_detail(identity)["attempt"]["id"]
    # Persist the public Controller boundary state left by a process dying
    # between its terminal delivery write and independent restoration result.
    ctrl.update_announcement_attempt(
        attempt_id,
        status="finished",
        finished_at="2027-01-15T12:00:05+00:00",
        media_restore="pending",
    )
    with pytest.raises(AnnouncementStateError):
        service.delete_history("tests", identity)
    fresh_doubles = FakeSynthesizer(), FakeCast()
    fresh = build_service(controller(engine, tmp_path), clock, fresh_doubles, tmp_path / "fresh")
    fresh.recover()
    detail = fresh.speaker_test_detail(identity)
    assert detail["attempt"]["status"] == "finished"
    assert detail["attempt"]["volume_restore"] == "unknown"
    assert detail["attempt"]["media_restore"] == "unknown"
    fresh.delete_history("tests", identity)
    fresh.tick()
    assert fresh_doubles[1].plays == []
