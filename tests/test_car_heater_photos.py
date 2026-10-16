"""Session-linked observation photos through authenticated public endpoints."""

from uuid import uuid4

import pytest
import test_car_heater_sessions as evidence
from test_car_heater_history_preservation import isolated_postgres as isolated_postgres

from app.core import Controller

session_app = evidence.session_app
client = evidence.client
headers = evidence.headers

JPEG = b"\xff\xd8\xff\xe0" + b"windshield-frame" * 64 + b"\xff\xd9"


def new_session(app, client, auth):
    response = client.post(
        "/api/car_heater/sessions",
        json={"trigger": "observation", "observed_at": "2026-10-15T06:00:00+03:00"},
        headers=auth,
    )
    assert response.status_code == 201
    return response.get_json()["id"]


def upload(client, auth, session_id, photo_id=None, content=JPEG, **fields):
    form = {
        "captured_at": "2026-10-15T03:10:04.250Z",
        "due_at": "2026-10-15T03:10:00Z",
        "capture_kind": "scheduled",
        "sequence": "1",
        "observation_started_at": "2026-10-15T03:00:00Z",
        "camera_label": "Back Ultra Wide Camera",
        **fields,
    }
    return client.put(
        f"/api/car_heater/sessions/{session_id}/photos/{photo_id or uuid4()}",
        query_string={key: value for key, value in form.items() if value is not None},
        data=content,
        headers=auth,
        content_type="image/jpeg",
    )


def reload_controller(app):
    app.ctrl = Controller(db_path=app.config["DB_PATH"])
    app.ctrl._sa_engine = app.extensions["session_engine"]


def test_saved_photo_survives_restart_with_actual_and_due_times(session_app, client):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    photo_id = str(uuid4())
    saved = upload(client, auth, session_id, photo_id)
    assert saved.status_code == 201
    ack = saved.get_json()
    assert ack["id"] == photo_id
    assert ack["session_id"] == session_id
    assert ack["captured_at"] == "2026-10-15T03:10:04.250000+00:00"
    assert ack["due_at"] == "2026-10-15T03:10:00+00:00"
    assert ack["capture_kind"] == "scheduled"
    assert ack["byte_size"] == len(JPEG)

    reload_controller(session_app)
    detail = client.get(f"/api/car_heater/sessions/{session_id}").get_json()
    assert [p["id"] for p in detail["photos"]] == [photo_id]
    assert detail["photos"][0]["captured_at"] != detail["photos"][0]["due_at"]
    image = client.get(f"/api/car_heater/sessions/{session_id}/photos/{photo_id}")
    assert image.status_code == 200
    assert image.data == JPEG
    assert image.mimetype == "image/jpeg"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    listed = client.get("/api/car_heater/sessions").get_json()
    assert listed[0]["photos"] == {
        "saved": 1,
        "first_captured_at": "2026-10-15T03:10:04.250000+00:00",
        "last_captured_at": "2026-10-15T03:10:04.250000+00:00",
    }


def test_retry_is_idempotent_and_reused_ids_cannot_replace_evidence(session_app, client):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    photo_id = str(uuid4())
    assert upload(client, auth, session_id, photo_id).status_code == 201
    # A retry after a lost acknowledgement returns the saved photo, not a duplicate.
    retried = upload(client, auth, session_id, photo_id)
    assert retried.status_code == 200
    assert retried.get_json()["id"] == photo_id
    replaced = upload(client, auth, session_id, photo_id, content=JPEG + b"other")
    assert replaced.status_code == 409
    other = new_session(session_app, client, auth)
    assert upload(client, auth, other, photo_id).status_code == 409
    assert len(client.get(f"/api/car_heater/sessions/{session_id}").get_json()["photos"]) == 1
    image = client.get(f"/api/car_heater/sessions/{session_id}/photos/{photo_id}")
    assert image.data == JPEG
    # Photos are only reachable through their own session.
    assert client.get(f"/api/car_heater/sessions/{other}/photos/{photo_id}").status_code == 404


def test_manual_capture_has_no_due_time(session_app, client):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    manual = upload(
        client, auth, session_id, capture_kind="manual", due_at=None, sequence=None
    ).get_json()
    assert manual["capture_kind"] == "manual"
    assert manual["due_at"] is None
    assert manual["sequence"] is None
    # A manual photo must not claim a scheduled due time.
    claimed = upload(client, auth, session_id, capture_kind="manual")
    assert claimed.status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        {"photo_id": "../../etc/passwd"},
        {"photo_id": "ABCDEF00-0000-4000-8000-000000000000"},
        {"content": b"<svg onload=alert(1)>"},
        {"content": b""},
        {"captured_at": None},
        {"captured_at": "2026-10-15T06:10"},
        {"captured_at": "2999-01-01T00:00:00Z"},
        {"due_at": None},
        {"capture_kind": "catch_up"},
        {"sequence": "-1"},
        {"camera_label": "x" * 201},
    ],
)
def test_invalid_photos_are_rejected_without_saving(session_app, client, change):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    response = upload(client, auth, session_id, **change)
    assert response.status_code in (400, 404, 411)
    assert client.get(f"/api/car_heater/sessions/{session_id}").get_json()["photos"] == []


def test_size_and_count_limits(session_app, client, monkeypatch):
    from app.blueprints.api.car_heater import photos

    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    monkeypatch.setattr(photos, "MAX_PHOTO_BYTES", len(JPEG) - 1)
    assert upload(client, auth, session_id).status_code == 413
    monkeypatch.setattr(photos, "MAX_PHOTO_BYTES", len(JPEG))
    monkeypatch.setattr(photos, "MAX_PHOTOS_PER_SESSION", 1)
    assert upload(client, auth, session_id).status_code == 201
    assert upload(client, auth, session_id).status_code == 409
    assert len(client.get(f"/api/car_heater/sessions/{session_id}").get_json()["photos"]) == 1


def test_photos_require_authentication_csrf_and_an_existing_session(session_app, client):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    photo_id = str(uuid4())
    assert upload(client, {}, session_id, photo_id).status_code == 400  # CSRF missing
    assert upload(client, auth, "unknown-session").status_code == 404
    assert upload(client, auth, session_id, photo_id).status_code == 201
    anonymous = session_app.test_client()
    assert (
        anonymous.get(f"/api/car_heater/sessions/{session_id}/photos/{photo_id}").status_code == 401
    )
    # CSRF is checked before login, so an anonymous upload fails closed either way.
    assert upload(anonymous, auth, session_id).status_code in (400, 401)


def test_photo_saves_never_call_heater_controls(session_app, client):
    class NoControls:
        def __getattr__(self, name):
            pytest.fail(f"Observation must not call heater service: {name}")

    session_app.car_heater_service = NoControls()
    session_app.departure_service = NoControls()
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    assert upload(client, auth, session_id).status_code == 201
    assert upload(client, auth, session_id, capture_kind="manual", due_at=None).status_code == 201


def assert_restored_photo(app, client, engine, session_id, photo_id):
    """A fresh Controller on the restored database serves the same association."""
    app.ctrl = Controller(db_path=app.config["DB_PATH"])
    app.ctrl._sa_engine = engine
    detail = client.get(f"/api/car_heater/sessions/{session_id}").get_json()
    assert [p["id"] for p in detail["photos"]] == [photo_id]
    assert detail["photos"][0]["captured_at"] == "2026-10-15T03:10:04.250000+00:00"
    image = client.get(f"/api/car_heater/sessions/{session_id}/photos/{photo_id}")
    assert image.data == JPEG
    # The restored copy keeps accepting evidence after restart.
    assert upload(client, headers(app, client), session_id).status_code == 201


def test_sqlite_backup_restore_keeps_photo_content_and_association(session_app, client, tmp_path):
    import json
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine

    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    photo_id = str(uuid4())
    assert upload(client, auth, session_id, photo_id).status_code == 201
    source = tmp_path / "sessions.db"
    backup, restored = tmp_path / "backup.db", tmp_path / "restored.db"
    for frm, to in [(source, backup), (backup, restored)]:
        subprocess.run(
            [sys.executable, "scripts/heater_history.py", "sqlite-copy", str(frm), str(to)],
            check=True,
            capture_output=True,
        )
    report = json.loads(
        subprocess.run(
            [sys.executable, "scripts/heater_history.py", "discover"],
            env={**os.environ, "DATABASE_URL": f"sqlite:///{restored}"},
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert report["tables"]["car_heater_photo"]["count"] == 1
    assert report["tables"]["car_heater_photo"]["oldest"] == "2026-10-15T03:10:04.250000+00:00"
    assert report["photo_integrity"] == {"checked": 1, "mismatched": [], "orphaned": []}
    engine = create_engine(f"sqlite:///{restored}")
    try:
        assert_restored_photo(session_app, client, engine, session_id, photo_id)
    finally:
        engine.dispose()


def test_discovery_reports_damaged_or_orphaned_photo_content(session_app, client, tmp_path):
    from scripts.heater_history import discover
    from sqlalchemy import text

    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    damaged, orphan = str(uuid4()), str(uuid4())
    assert upload(client, auth, session_id, damaged).status_code == 201
    assert upload(client, auth, session_id, orphan).status_code == 201
    with session_app.extensions["session_engine"].begin() as conn:
        conn.execute(
            text("UPDATE car_heater_photo SET content = :c WHERE id = :i"),
            {"c": b"x", "i": damaged},
        )
        conn.execute(
            text("UPDATE car_heater_photo SET session_id = 'gone' WHERE id = :i"), {"i": orphan}
        )
    report = discover(f"sqlite:///{tmp_path / 'sessions.db'}")
    assert report["photo_integrity"] == {
        "checked": 2,
        "mismatched": [damaged],
        "orphaned": [orphan],
    }


def test_postgresql_dump_restore_keeps_photo_content_and_association(
    session_app, client, tmp_path, request
):
    import subprocess

    from scripts.heater_history import discover
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    from app.core.schema import (
        metadata,
    )

    binaries, socket = request.getfixturevalue("isolated_postgres")
    url = URL.create(
        "postgresql+psycopg", database="postgres", query={"host": str(socket), "port": "55439"}
    )
    engine = create_engine(url)
    metadata.create_all(
        engine,
        tables=[
            metadata.tables[name]
            for name in (
                "car_heater_sensor_frame",
                "car_heater_session",
                "car_heater_execution",
                "car_heater_departure_plan",
                "car_heater_photo",
                "car_heater_observation",
            )
        ],
    )
    original = session_app.ctrl._sa_engine
    session_app.ctrl._sa_engine = engine
    restored = None
    try:
        auth = headers(session_app, client)
        session_id = new_session(session_app, client, auth)
        photo_id = str(uuid4())
        assert upload(client, auth, session_id, photo_id).status_code == 201
        archive = tmp_path / "photos.dump"
        pg = ["-h", str(socket), "-p", "55439"]
        subprocess.run(
            [
                str(binaries / "pg_dump"),
                *pg,
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
                *pg,
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
        target = url.set(database="restored")
        report = discover(target.render_as_string(hide_password=False))
        assert report["photo_integrity"] == {"checked": 1, "mismatched": [], "orphaned": []}
        restored = create_engine(target)
        assert_restored_photo(session_app, client, restored, session_id, photo_id)
    finally:
        session_app.ctrl._sa_engine = original
        engine.dispose()
        if restored is not None:
            restored.dispose()


def put_observation(client, auth, session_id, observation_id, body):
    return client.put(
        f"/api/car_heater/sessions/{session_id}/observations/{observation_id}",
        json=body,
        headers=auth,
    )


def test_observation_window_and_missed_slots_merge_without_resetting(session_app, client):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    obs = str(uuid4())
    started = {"started_at": "2026-10-15T03:00:00Z"}
    assert (
        put_observation(client, auth, session_id, obs, {**started, "missed": []}).status_code == 201
    )
    miss = {"sequence": 2, "due_at": "2026-10-15T03:20:00Z", "reason": "page_hidden"}
    assert (
        put_observation(client, auth, session_id, obs, {**started, "missed": [miss]}).status_code
        == 200
    )
    # A partial/older update must not drop recorded misses or clear the finish.
    finish = {**started, "finished_at": "2026-10-15T03:45:00Z", "missed": []}
    assert put_observation(client, auth, session_id, obs, finish).status_code == 200
    assert (
        put_observation(client, auth, session_id, obs, {**started, "missed": []}).status_code == 200
    )
    reload_controller(session_app)
    [saved] = client.get(f"/api/car_heater/sessions/{session_id}").get_json()["observations"]
    assert saved["id"] == obs
    assert saved["started_at"] == "2026-10-15T03:00:00+00:00"
    assert saved["finished_at"] == "2026-10-15T03:45:00+00:00"
    assert saved["missed"] == [
        {"sequence": 2, "due_at": "2026-10-15T03:20:00+00:00", "reason": "page_hidden"}
    ]
    # Start time is immutable and the record belongs to one session.
    moved = {"started_at": "2026-10-15T03:05:00Z", "missed": []}
    assert put_observation(client, auth, session_id, obs, moved).status_code == 409
    other = new_session(session_app, client, auth)
    assert put_observation(client, auth, other, obs, {**started, "missed": []}).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"missed": []},
        {"started_at": "2026-10-15T03:00", "missed": []},
        {"started_at": "2026-10-15T03:00:00Z", "missed": "none"},
        {"started_at": "2026-10-15T03:00:00Z", "missed": [{"sequence": -1}]},
        {
            "started_at": "2026-10-15T03:00:00Z",
            "missed": [{"sequence": 1, "due_at": "2026-10-15T03:10:00Z", "reason": "bored"}],
        },
        {
            "started_at": "2026-10-15T03:00:00Z",
            "missed": [
                {"sequence": n, "due_at": "2026-10-15T03:10:00Z", "reason": "page_hidden"}
                for n in range(1001)
            ],
        },
        {"started_at": "2026-10-15T03:00:00Z", "finished_at": "2026-10-15T02:00:00Z", "missed": []},
    ],
)
def test_invalid_observation_records_are_rejected(session_app, client, body):
    auth = headers(session_app, client)
    session_id = new_session(session_app, client, auth)
    assert put_observation(client, auth, session_id, str(uuid4()), body).status_code == 400
    assert put_observation(client, {}, session_id, str(uuid4()), {}).status_code == 400
    assert client.get(f"/api/car_heater/sessions/{session_id}").get_json()["observations"] == []
