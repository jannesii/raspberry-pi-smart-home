"""Announcing through the owner's existing Google Home speaker group (#37).

Public operations only: the Root-Admin API and the service's ``tick()`` with a
controlled clock, real Controller and isolated SQLite. Cast is the simulated
``FakeCast`` whose group receiver (``GROUP``) plays on its reachable members;
nothing contacts a speaker. ``START`` is 2027-01-15 12:00 UTC (14:00 Helsinki).
"""

from __future__ import annotations

from datetime import datetime

import announcement_support as support
import pytest
from announcement_support import GROUP, build_service, group_targets, headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord

API = "/api/announcements"
RIGHT = "192.168.1.119"
LEFT = "192.168.1.148"


@pytest.fixture
def group_app(announcement_app, clock, doubles, discord, tmp_path):
    """The app with the group configured (stable identities for every member)."""
    announcement_app.announcement_service = build_service(
        announcement_app.ctrl,
        clock,
        doubles,
        tmp_path / "cache",
        discord=discord,
        targets=group_targets(),
    )
    return announcement_app


def at(clock, iso_utc):
    clock[0] = datetime.fromisoformat(iso_utc).timestamp()


def request_test(app, client, **body):
    payload = {"text": "Muista ottaa lääkkeet.", "target": "jbl-group", **body}
    response = client.post(f"{API}/tests", json=payload, headers=headers(app, client))
    assert response.status_code == 202, response.get_json()
    return response.get_json()["test"]["id"]


def attempt_of(client, test_id):
    return client.get(f"{API}/tests/{test_id}").get_json()["attempt"]


def save(app, client, **body):
    payload = {
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-group",
        "due_local": "2027-01-15T14:30",
        **body,
    }
    response = client.post(f"{API}/schedules", json=payload, headers=headers(app, client))
    assert response.status_code == 201, response.get_json()
    return response.get_json()["schedule"]


def occurrence_of(client, schedule_id):
    detail = client.get(f"{API}/schedules/{schedule_id}").get_json()
    (item,) = detail["occurrences"]
    return client.get(f"{API}/occurrences/{item['occurrence']['id']}").get_json()


def plays(cast):
    return [effect[1] for effect in cast.effects if effect[0] == "play"]


def members(attempt):
    return {m["key"]: m for m in attempt["member_evidence"]}


def test_group_test_plays_once_on_the_group_with_group_scoped_evidence(group_app, client, doubles):
    _synth, cast = doubles
    service = group_app.announcement_service
    test_id = request_test(group_app, client)
    service.tick()

    attempt = attempt_of(client, test_id)
    assert attempt["status"] == "dispatched"
    assert attempt["evidence_scope"] == "group"
    assert attempt["speaker_set"] == ["jbl-left", "jbl-right"]
    assert attempt["target_device_id"] == "uuid-group"
    assert plays(cast) == ["jbl-group"]  # one synchronized stream, never per speaker
    assert attempt["member_evidence"] == [
        {
            "key": "jbl-right",
            "name": "Jbl-Right",
            "availability": "available",
            "in_group": True,
            "left_group_during_playback": None,
        },
        {
            "key": "jbl-left",
            "name": "Jbl-Left",
            "availability": "available",
            "in_group": True,
            "left_group_during_playback": None,
        },
    ]

    cast.report(GROUP, attempt["id"], "playing")
    service.tick()
    cast.report(GROUP, attempt["id"], "finished")
    service.tick()
    attempt = attempt_of(client, test_id)
    assert attempt["status"] == "finished"
    assert {k: m["left_group_during_playback"] for k, m in members(attempt).items()} == {
        "jbl-right": False,
        "jbl-left": False,
    }
    # Nothing per member is invented: no audibility or acknowledgement fields.
    assert {key for m in attempt["member_evidence"] for key in m} == {
        "key",
        "name",
        "availability",
        "in_group",
        "left_group_during_playback",
    }
    assert plays(cast) == ["jbl-group"]


def test_unreachable_member_makes_delivery_partial_and_names_it_without_replay(
    group_app, client, doubles, discord, clock
):
    _synth, cast = doubles
    service = group_app.announcement_service
    cast.speakers[LEFT].state = "unavailable"
    schedule = save(group_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    (attempt,) = occurrence_of(client, schedule["id"])["attempts"]
    assert plays(cast) == ["jbl-group"]  # the reachable member still hears the group
    assert members(attempt)["jbl-left"]["availability"] == "unavailable"
    assert members(attempt)["jbl-left"]["in_group"] is False
    cast.report(GROUP, attempt["id"], "playing")
    service.tick()
    cast.report(GROUP, attempt["id"], "finished")
    at(clock, "2027-01-15T12:30:20+00:00")
    service.tick()

    item = occurrence_of(client, schedule["id"])
    occurrence = item["occurrence"]
    assert occurrence["status"] == "closed"
    assert occurrence["delivery"] == "partial"
    assert occurrence["delivery_reason"] == "member_unavailable"
    assert occurrence["fallback_reason"] == "partial"
    assert item["fallback"]["status"] == "accepted"
    (message,) = discord.messages
    assert message.startswith("**Reminder only partly spoken** · Both JBLs")
    assert "Only part of Both JBLs reported playback: Jbl-Left did not take part." in message

    # The successful member is never replayed to reach the missing one, and the
    # missing one never gets an unsynchronized stream of its own.
    cast.speakers[LEFT].state = "idle"
    for minute in (31, 35, 40, 50):
        at(clock, f"2027-01-15T12:{minute}:00+00:00")
        service.tick()
    assert plays(cast) == ["jbl-group"]
    assert len(discord.messages) == 1


def test_member_leaving_the_group_during_playback_is_partial(group_app, client, doubles, clock):
    _synth, cast = doubles
    service = group_app.announcement_service
    schedule = save(group_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    (attempt,) = occurrence_of(client, schedule["id"])["attempts"]
    cast.report(GROUP, attempt["id"], "playing")
    cast.speakers[RIGHT].state = "unavailable"  # drops out of the group mid-speech
    service.tick()
    cast.report(GROUP, attempt["id"], "finished")
    service.tick()
    item = occurrence_of(client, schedule["id"])
    (attempt,) = item["attempts"]
    assert members(attempt)["jbl-right"]["availability"] == "available"  # at dispatch
    assert members(attempt)["jbl-right"]["left_group_during_playback"] is True
    assert members(attempt)["jbl-left"]["left_group_during_playback"] is False
    assert item["occurrence"]["delivery"] == "partial"
    assert item["occurrence"]["fallback_reason"] == "partial"


def test_group_report_without_member_evidence_is_delivered_at_group_scope_only(
    group_app, client, doubles, discord, clock, tmp_path
):
    _synth, cast = doubles
    group_app.announcement_service = build_service(
        group_app.ctrl,
        clock,
        doubles,
        tmp_path / "cache",
        discord=discord,
        targets=group_targets(member_ids=False),
    )
    service = group_app.announcement_service
    cast.speakers[GROUP].membership_reported = False
    schedule = save(group_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    (attempt,) = occurrence_of(client, schedule["id"])["attempts"]
    cast.report(GROUP, attempt["id"], "playing")
    service.tick()
    cast.report(GROUP, attempt["id"], "finished")
    service.tick()
    item = occurrence_of(client, schedule["id"])
    (attempt,) = item["attempts"]
    assert attempt["evidence_scope"] == "group"
    assert [
        (m["availability"], m["in_group"], m["left_group_during_playback"])
        for m in attempt["member_evidence"]
    ] == [("available", None, None)] * 2
    # Group playback is reported as such: no member confirmation is invented,
    # and nothing claims anybody heard it or acknowledged the reminder.
    assert item["occurrence"]["delivery"] == "delivered"
    assert item["occurrence"]["fallback_reason"] is None
    assert item["acknowledgement"] is None
    service.tick()
    assert discord.messages == []


def test_unavailable_group_fails_visibly_and_never_substitutes_separate_streams(
    group_app, client, doubles, discord, clock
):
    _synth, cast = doubles
    service = group_app.announcement_service
    cast.speakers[GROUP].state = "unavailable"  # both JBLs still answer on their own
    test_id = request_test(group_app, client)
    schedule = save(group_app, client)
    service.tick()
    attempt = attempt_of(client, test_id)
    assert attempt["status"] == "failed"
    assert attempt["reason"] == "group_unavailable"
    assert [m["availability"] for m in attempt["member_evidence"]] == ["available"] * 2

    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    item = occurrence_of(client, schedule["id"])
    assert item["occurrence"]["waiting_reason"] == "group_unavailable"
    at(clock, "2027-01-15T12:45:00+00:00")
    service.tick()
    item = occurrence_of(client, schedule["id"])
    assert item["occurrence"]["disposition"] == "missed"
    assert item["occurrence"]["fallback_reason"] == "expired_undelivered"
    (message,) = discord.messages
    assert "while waiting for the speaker group to respond" in message
    assert plays(cast) == []  # neither the group nor any member was played


def test_unconfigured_group_is_shown_and_cannot_be_selected(announcement_app, client, doubles):
    _synth, cast = doubles
    status = client.get(f"{API}/status").get_json()
    group = next(t for t in status["targets"] if t["key"] == "jbl-group")
    assert group["kind"] == "group"
    assert group["configured"] is False
    assert group["availability"] == "unconfigured"
    assert [m["key"] for m in group["member_status"]] == ["jbl-right", "jbl-left"]
    for url, body in (
        (f"{API}/tests", {"text": "Hei", "target": "jbl-group"}),
        (
            f"{API}/schedules",
            {"text": "Hei", "target": "jbl-group", "due_local": "2027-01-15T14:30"},
        ),
    ):
        response = client.post(url, json=body, headers=headers(announcement_app, client))
        assert response.status_code == 400
        error = response.get_json()
        assert error["field"] == "target"
        assert "not configured" in error["message"]
    assert cast.effects == []


def test_group_and_member_announcements_never_overlap(group_app, client, doubles, clock):
    _synth, cast = doubles
    service = group_app.announcement_service
    right = request_test(group_app, client, target="jbl-right")
    clock[0] += 1
    grouped = request_test(group_app, client)
    service.tick()
    assert attempt_of(client, right)["status"] == "dispatched"
    assert attempt_of(client, grouped)["status"] == "requested"  # shares Jbl-Right
    cast.report(RIGHT, attempt_of(client, right)["id"], "finished")
    service.tick()
    assert attempt_of(client, grouped)["status"] == "dispatched"
    clock[0] += 1
    left = request_test(group_app, client, target="jbl-left")
    service.tick()
    assert attempt_of(client, left)["status"] == "requested"  # the group holds Jbl-Left
    cast.report(GROUP, attempt_of(client, grouped)["id"], "finished")
    service.tick()
    assert attempt_of(client, left)["status"] == "dispatched"
    assert plays(cast) == ["jbl-right", "jbl-group", "jbl-left"]


def test_urgent_group_reminder_waits_for_current_speech_but_goes_first_and_still_expires(
    group_app, client, doubles, clock
):
    _synth, cast = doubles
    service = group_app.announcement_service
    ordinary = save(group_app, client, target="jbl-left", due_local="2027-01-15T14:31")
    urgent = save(group_app, client, urgent=True, due_local="2027-01-15T14:31")
    late = save(group_app, client, urgent=True, due_local="2027-01-15T14:31", expiry_minutes=1)
    at(clock, "2027-01-15T12:30:50+00:00")
    test_id = request_test(group_app, client, target="jbl-right")
    service.tick()
    speaking = attempt_of(client, test_id)
    assert speaking["status"] == "dispatched"
    cast.report(RIGHT, speaking["id"], "playing")

    at(clock, "2027-01-15T12:31:05+00:00")
    service.tick()
    assert occurrence_of(client, urgent["id"])["occurrence"]["waiting_reason"] == "speaker_busy"
    assert occurrence_of(client, late["id"])["occurrence"]["waiting_reason"] == "speaker_busy"
    # The free Jbl-Left still waits: the urgent group reminders are ahead of it.
    assert occurrence_of(client, ordinary["id"])["occurrence"]["waiting_reason"] == "speaker_busy"
    assert plays(cast) == ["jbl-right"]  # current speech is never cut off

    at(clock, "2027-01-15T12:32:00+00:00")
    cast.report(RIGHT, speaking["id"], "finished")
    service.tick()
    assert plays(cast) == ["jbl-right", "jbl-group"]
    assert occurrence_of(client, urgent["id"])["occurrence"]["status"] == "announcing"
    late_item = occurrence_of(client, late["id"])["occurrence"]
    assert late_item["disposition"] == "missed"  # queueing never extends expiry
    assert late_item["fallback_reason"] == "expired_undelivered"
    assert occurrence_of(client, ordinary["id"])["occurrence"]["status"] == "waiting"


def test_music_on_one_member_holds_an_ordinary_group_reminder(group_app, client, doubles, clock):
    _synth, cast = doubles
    service = group_app.announcement_service
    cast.speakers[LEFT].state = "playing"  # Spotify on Jbl-Left only
    schedule = save(group_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    assert occurrence_of(client, schedule["id"])["occurrence"]["waiting_reason"] == "music_playing"
    assert plays(cast) == []


def test_group_volume_and_stop_respect_newer_intent_and_keep_uncertainty(
    group_app, client, doubles, clock
):
    _synth, cast = doubles
    service = group_app.announcement_service
    cast.speakers[GROUP].volume = 0.5
    first = request_test(group_app, client, volume_percent=30)
    service.tick()
    attempt = attempt_of(client, first)
    assert ("set_volume", "jbl-group", 0.3) in cast.effects
    cast.speakers[GROUP].volume = 0.7  # the owner turned the group up meanwhile
    cast.report(GROUP, attempt["id"], "finished")
    service.tick()
    attempt = attempt_of(client, first)
    assert attempt["volume_restore"] == "skipped_newer_intent"
    assert cast.speakers[GROUP].volume == 0.7

    clock[0] += 5
    second = request_test(group_app, client, volume_percent=70)
    service.tick()
    cast.stop_result = "unknown"
    response = client.post(
        f"{API}/tests/{second}/stop", json={}, headers=headers(group_app, client)
    )
    assert response.status_code == 202
    service.tick()
    attempt = attempt_of(client, second)
    assert attempt["stop_outcome"] == "unknown"  # never claimed stopped
    assert attempt["status"] in ("dispatched", "playing")
    assert ("stop", "jbl-group", attempt["id"]) in cast.effects
    assert not any(e[0] == "stop" and e[1] != "jbl-group" for e in cast.effects)


def test_restart_during_group_playback_is_unknown_and_not_replayed(
    group_app, client, doubles, discord, clock, tmp_path
):
    _synth, cast = doubles
    schedule = save(group_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    group_app.announcement_service.tick()
    group_app.announcement_service = build_service(
        group_app.ctrl,
        clock,
        doubles,
        tmp_path / "cache",
        discord=discord,
        targets=group_targets(),
    )
    group_app.announcement_service.tick()
    item = occurrence_of(client, schedule["id"])
    assert item["occurrence"]["delivery"] == "unknown"
    assert item["occurrence"]["fallback_reason"] == "unknown"
    assert plays(cast) == ["jbl-group"]


def test_discovery_finds_the_existing_group_and_suggests_configuration_read_only(
    announcement_app, client, doubles
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers[LEFT].state = "unavailable"
    response = client.post(f"{API}/discovery", json={}, headers=headers(announcement_app, client))
    assert response.status_code == 202
    assert response.get_json()["discovery"]["status"] == "requested"
    assert cast.effects == []  # the request itself contacts nothing
    service.tick()

    status = client.get(f"{API}/status").get_json()
    discovery = status["discovery"]
    assert discovery["status"] == "finished"
    found = {(d["name"], d["cast_type"], d["port"], d["device_id"]) for d in discovery["devices"]}
    assert found == {
        ("Jbl-Right", "audio", 8009, "uuid-right"),
        ("Koti kaiuttimet", "group", 32187, "uuid-group"),
    }
    suggested = {t["key"]: t for t in discovery["suggested_targets"]}
    assert suggested["jbl-right"]["device_id"] == "uuid-right"
    assert suggested["jbl-left"].get("device_id") is None  # not found: nothing invented
    assert suggested["jbl-group"] == {
        "key": "jbl-group",
        "name": "Both JBLs (group)",
        "kind": "group",
        "host": "192.168.1.119",
        "port": 32187,
        "device_id": "uuid-group",
        "members": ["jbl-right", "jbl-left"],
    }
    by_key = {t["key"]: t for t in status["targets"]}
    assert by_key["jbl-right"]["availability"] == "available"
    assert by_key["jbl-left"]["availability"] == "unavailable"
    assert by_key["jbl-group"]["availability"] == "unconfigured"  # until the owner sets it
    # Discovery only reads: no group is created or changed, nothing plays.
    assert {e[0] for e in cast.effects} == {"discover"}


def test_group_status_reports_member_availability_after_a_check(group_app, client, doubles):
    _synth, cast = doubles
    cast.speakers[LEFT].state = "unavailable"
    client.post(f"{API}/discovery", json={}, headers=headers(group_app, client))
    group_app.announcement_service.tick()
    status = client.get(f"{API}/status").get_json()
    group = next(t for t in status["targets"] if t["key"] == "jbl-group")
    assert group["configured"] is True
    assert group["availability"] == "available"
    assert [(m["key"], m["availability"]) for m in group["member_status"]] == [
        ("jbl-right", "available"),
        ("jbl-left", "unavailable"),
    ]


def test_discovery_is_root_admin_and_csrf_protected(announcement_app, client):
    anonymous = announcement_app.test_client()
    assert anonymous.post(f"{API}/discovery", json={}).status_code in (400, 401)
    admin = support.login(announcement_app.test_client(), "admin")
    denied = admin.post(f"{API}/discovery", json={}, headers=headers(announcement_app, admin))
    assert denied.status_code == 403
    assert client.post(f"{API}/discovery", json={}).status_code == 400  # missing CSRF
    status = client.get(f"{API}/status").get_json()
    assert status["discovery"]["status"] == "idle"


def test_group_configuration_requires_known_member_speakers():
    import json

    from app.services.announcements.config import parse_targets

    speakers = [
        {"key": "jbl-right", "name": "Jbl-Right", "host": "192.168.1.119"},
        {"key": "jbl-left", "name": "Jbl-Left", "host": "192.168.1.148"},
    ]
    group = {
        "key": "jbl-group",
        "name": "Both JBLs",
        "kind": "group",
        "host": "192.168.1.119",
        "port": 32187,
        "device_id": "uuid-group",
        "members": ["jbl-right", "jbl-left"],
    }
    (_right, _left, parsed) = parse_targets(json.dumps([*speakers, group]))
    assert parsed.configured and parsed.is_group
    assert parsed.speaker_set == {"jbl-right", "jbl-left"}
    without_identity = parse_targets(json.dumps([*speakers, {**group, "device_id": None}]))[2]
    assert without_identity.configured is False  # never addressed by host alone
    for broken in (
        {**group, "members": ["jbl-right"]},
        {**group, "members": ["jbl-right", "kitchen"]},
        {**group, "kind": "stereo-pair"},
        {**speakers[0], "key": "solo", "members": ["jbl-left"]},
    ):
        with pytest.raises(ValueError):
            parse_targets(json.dumps([*speakers, broken]))


class _Info:
    def __init__(self, uuid, name, cast_type, host, port):
        self.uuid, self.friendly_name, self.cast_type = uuid, name, cast_type
        self.host, self.port, self.model_name = host, port, "model"


class _Cast:
    def __init__(self, info):
        self.cast_info = info
        self.uuid = info.uuid
        self.handlers = []
        self.is_idle = True
        self.status = type("S", (), {"app_id": None, "volume_level": 0.25})()
        self.media_controller = type("M", (), {"status": None})()

    def wait(self, timeout=None):
        pass

    def disconnect(self, timeout=None):
        pass

    def register_handler(self, handler):
        self.handlers.append(handler)


def _fake_pychromecast(monkeypatch, casts):
    """Stands in for the PyChromecast library (not installed; no network)."""
    import sys
    import types
    from uuid import UUID

    lookups = []

    def get_listed_chromecasts(known_hosts=None, uuids=None, discovery_timeout=None, **_):
        lookups.append({"known_hosts": known_hosts, "uuids": uuids})
        wanted = set(uuids or ())
        return [c for c in casts if not wanted or c.uuid in wanted], object()

    class MultizoneController:
        def __init__(self, uuid):
            self.uuid = uuid
            self.members = []

        def update_members(self):
            self.members = [UUID(int=1), UUID(int=2)]

    root = types.ModuleType("pychromecast")
    root.get_listed_chromecasts = get_listed_chromecasts
    root.get_chromecasts = lambda known_hosts=None, **_: (list(casts), object())
    root.discovery = types.SimpleNamespace(stop_discovery=lambda browser: None)
    multizone = types.ModuleType("pychromecast.controllers.multizone")
    multizone.MultizoneController = MultizoneController
    monkeypatch.setitem(sys.modules, "pychromecast", root)
    monkeypatch.setitem(sys.modules, "pychromecast.controllers", types.ModuleType("c"))
    monkeypatch.setitem(sys.modules, "pychromecast.controllers.multizone", multizone)
    return lookups


def test_cast_adapter_addresses_the_group_by_uuid_never_its_leader_speaker(monkeypatch):
    from uuid import UUID

    from app.services.announcements import PyChromecastTransport, SpeakerTarget

    leader = _Cast(_Info(UUID(int=1), "Jbl-Right", "audio", "192.168.1.119", 8009))
    group = _Cast(_Info(UUID(int=9), "Koti kaiuttimet", "group", "192.168.1.119", 32187))
    lookups = _fake_pychromecast(monkeypatch, [leader, group])
    transport = PyChromecastTransport(membership_timeout_s=0)
    target = SpeakerTarget(
        key="jbl-group",
        name="Both JBLs",
        host="192.168.1.119",
        port=32187,
        device_id=str(UUID(int=9)),
        kind="group",
        members=("jbl-right", "jbl-left"),
    )
    probe = transport.probe(target)
    assert probe.state == "idle"
    assert probe.device_id == str(UUID(int=9))
    assert probe.member_ids == (str(UUID(int=1)), str(UUID(int=2)))
    assert lookups[-1]["uuids"] == [UUID(int=9)]
    # Without the group's UUID the adapter refuses rather than reach the leader.
    unidentified = SpeakerTarget(
        key="g", name="g", host="192.168.1.119", port=32187, kind="group", members=("a", "b")
    )
    assert transport.probe(unidentified).state == "unavailable"
    # A speaker located only by host never resolves to the group at that host.
    right = SpeakerTarget(key="jbl-right", name="Jbl-Right", host="192.168.1.119")
    assert transport.probe(right).device_id == str(UUID(int=1))
    assert transport.probe(right).member_ids is None

    found = transport.discover(["192.168.1.119"])
    assert {(d["name"], d["cast_type"], d["port"]) for d in found} == {
        ("Jbl-Right", "audio", 8009),
        ("Koti kaiuttimet", "group", 32187),
    }
