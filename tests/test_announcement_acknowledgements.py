"""Done, Skip, Stop speaking and bounded repeats through public operations.

Real Controller, isolated SQLite, real service policy; doubles only for the
Piper subprocess and the Cast transport. ``START`` is 2027-01-15 12:00 UTC,
i.e. 14:00 in Europe/Helsinki (UTC+2 in winter). The reminder used here is
due at 14:30 Helsinki (12:30 UTC) and expires 15 minutes later by default.
"""

from __future__ import annotations

from datetime import datetime

import announcement_support as support
from announcement_support import headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles

API = "/api/announcements"
RIGHT = "192.168.1.119"


def save(app, client, expect=201, **body):
    payload = {
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "due_local": "2027-01-15T14:30",
        **body,
    }
    response = client.post(f"{API}/schedules", json=payload, headers=headers(app, client))
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def at(clock, iso_utc):
    clock[0] = datetime.fromisoformat(iso_utc).timestamp()


def only_occurrence(client, schedule_id):
    items = client.get(f"{API}/schedules/{schedule_id}").get_json()["occurrences"]
    assert len(items) == 1
    return items[0]


def test_one_announcement_by_default_and_repeats_explain_what_fits_before_expiry(
    announcement_app, client
):
    plain = save(announcement_app, client)["schedule"]
    assert plain["max_announcements"] == 1
    assert plain["repeat_interval_minutes"] is None
    assert plain["repeats"] == {
        "max_announcements": 1,
        "repeat_interval_minutes": None,
        "fit": 1,
        "offsets_minutes": [0],
    }

    # Three announcements ten minutes apart cannot all fit in fifteen minutes.
    three = save(announcement_app, client, max_announcements=3, repeat_interval_minutes=10)
    assert three["schedule"]["repeats"] == {
        "max_announcements": 3,
        "repeat_interval_minutes": 10,
        "fit": 2,
        "offsets_minutes": [0, 10],
    }
    wide = save(
        announcement_app,
        client,
        max_announcements=3,
        repeat_interval_minutes=10,
        expiry_minutes=30,
    )["schedule"]
    assert wide["repeats"]["fit"] == 3
    assert wide["repeats"]["offsets_minutes"] == [0, 10, 20]


def rejected(app, client, field, **body):
    response = client.post(
        f"{API}/schedules",
        json={"text": "Hei", "target": "jbl-left", "due_local": "2027-01-15T15:00", **body},
        headers=headers(app, client),
    )
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["field"] == field, response.get_json()
    return response.get_json()["message"]


def test_repeat_settings_are_bounded_and_validated(announcement_app, client):
    rejected(announcement_app, client, "max_announcements", max_announcements=0)
    rejected(
        announcement_app,
        client,
        "max_announcements",
        max_announcements=6,
        repeat_interval_minutes=5,
    )
    rejected(announcement_app, client, "max_announcements", max_announcements=2.5)
    rejected(announcement_app, client, "max_announcements", max_announcements=True)
    message = rejected(announcement_app, client, "repeat_interval_minutes", max_announcements=2)
    assert "interval" in message
    rejected(
        announcement_app,
        client,
        "repeat_interval_minutes",
        max_announcements=2,
        repeat_interval_minutes=0,
    )
    rejected(
        announcement_app,
        client,
        "repeat_interval_minutes",
        max_announcements=2,
        repeat_interval_minutes=121,
    )
    # An interval without repeats is meaningless rather than silently ignored.
    rejected(announcement_app, client, "repeat_interval_minutes", repeat_interval_minutes=5)
    assert client.get(f"{API}/schedules").get_json()["schedules"] == []


def play_through(service, cast, clock):
    """The speaker plays the latest announcement to the end."""
    attempt_id = cast.plays[-1]["attempt_id"]
    cast.report(RIGHT, attempt_id, "playing")
    clock[0] += 1
    service.tick()
    cast.report(RIGHT, attempt_id, "finished")
    clock[0] += 4
    service.tick()


def test_repeats_keep_the_occurrence_and_deadline_and_never_burst(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=3, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    first = only_occurrence(client, sid)["occurrence"]
    assert first["status"] == "waiting"
    assert first["delivery"] == "delivered"
    assert first["announcements_started"] == 1
    assert first["next_attempt_at"] == "2027-01-15T12:34:00+00:00"
    assert first["fallback_reason"] is None

    at(clock, "2027-01-15T12:33:59+00:00")
    service.tick()
    assert len(cast.plays) == 1
    # The loop was stalled past two nominal repeats: one announcement, not a burst.
    at(clock, "2027-01-15T12:43:00+00:00")
    service.tick()
    service.tick()
    assert len(cast.plays) == 2
    play_through(service, cast, clock)
    item = only_occurrence(client, sid)
    occurrence = item["occurrence"]
    assert occurrence["id"] == first["id"]
    assert occurrence["expires_at"] == "2027-01-15T12:45:00+00:00"  # never extended
    assert occurrence["announcements_started"] == 2
    # The third would start at 12:47, after expiry: closed as delivered, not missed.
    assert occurrence["status"] == "closed"
    assert occurrence["delivery"] == "delivered"
    assert occurrence["disposition"] is None
    assert occurrence["fallback_reason"] is None
    assert [a["purpose"] for a in item["attempts"]] == ["announcement", "repeat"]
    assert {a["dispatch_deadline"] for a in item["attempts"]} == {"2027-01-15T12:45:00+00:00"}
    at(clock, "2027-01-15T13:30:00+00:00")
    service.tick()
    assert len(cast.plays) == 2


def test_waiting_repeat_that_reaches_expiry_closes_delivered_without_fallback(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=5)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    cast.speakers[RIGHT].state = "playing"  # music started; ordinary repeats wait
    at(clock, "2027-01-15T12:35:00+00:00")
    service.tick()
    assert only_occurrence(client, sid)["occurrence"]["waiting_reason"] == "music_playing"
    at(clock, "2027-01-15T12:45:00+00:00")
    service.tick()
    occurrence = only_occurrence(client, sid)["occurrence"]
    assert occurrence["status"] == "closed"
    assert occurrence["delivery"] == "delivered"
    assert occurrence["disposition"] is None
    assert occurrence["fallback_reason"] is None
    assert occurrence["announcements_started"] == 1
    assert len(cast.plays) == 1


def test_definitive_failure_is_retried_within_the_original_window_apart_from_repeats(
    announcement_app, client, doubles, clock
):
    from app.services.announcements import TransportError

    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=5)
    sid = saved["schedule"]["id"]
    cast.play_error = TransportError("connection_refused", certain_not_played=True)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    waiting = only_occurrence(client, sid)["occurrence"]
    assert waiting["status"] == "waiting"
    assert waiting["delivery"] == "failed"
    assert waiting["next_attempt_purpose"] == "retry"
    assert waiting["next_attempt_at"] == "2027-01-15T12:31:00+00:00"
    assert waiting["fallback_reason"] is None
    assert (waiting["announcements_started"], waiting["retries_started"]) == (1, 0)

    cast.play_error = None
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    retried = only_occurrence(client, sid)
    occurrence = retried["occurrence"]
    assert occurrence["delivery"] == "delivered"
    assert (occurrence["announcements_started"], occurrence["retries_started"]) == (1, 1)
    assert occurrence["next_attempt_purpose"] == "repeat"
    assert occurrence["next_attempt_at"] == "2027-01-15T12:36:00+00:00"

    at(clock, "2027-01-15T12:36:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    item = only_occurrence(client, sid)
    assert [(a["purpose"], a["status"], a["certain_not_played"]) for a in item["attempts"]] == [
        ("announcement", "failed", True),
        ("retry", "finished", None),
        ("repeat", "finished", None),
    ]
    assert item["occurrence"]["status"] == "closed"
    assert item["occurrence"]["announcements_started"] == 2
    assert item["occurrence"]["fallback_reason"] is None
    assert item["occurrence"]["expires_at"] == "2027-01-15T12:45:00+00:00"


def test_retries_are_bounded_and_never_extend_expiry(announcement_app, client, doubles, clock):
    from app.services.announcements import TransportError

    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.play_error = TransportError("connection_refused", certain_not_played=True)
    budget = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    for _ in range(10):
        service.tick()
        clock[0] += 60
    item = only_occurrence(client, budget["id"])
    assert [a["purpose"] for a in item["attempts"]] == ["announcement", "retry", "retry"]
    assert item["occurrence"]["status"] == "closed"
    assert item["occurrence"]["delivery"] == "failed"
    assert item["occurrence"]["retries_started"] == 2
    assert item["occurrence"]["fallback_reason"] == "failed"

    short = save(announcement_app, client, due_local="2027-01-15T15:00", expiry_minutes=2)
    at(clock, "2027-01-15T13:00:00+00:00")
    for _ in range(6):
        service.tick()
        clock[0] += 30
    item = only_occurrence(client, short["schedule"]["id"])
    # 13:00 fails, 13:01 retry fails, a retry at 13:02 would be at expiry.
    assert [a["requested_at"] for a in item["attempts"]] == [
        "2027-01-15T13:00:00+00:00",
        "2027-01-15T13:01:00+00:00",
    ]
    assert item["occurrence"]["expires_at"] == "2027-01-15T13:02:00+00:00"
    assert item["occurrence"]["fallback_reason"] == "failed"


def test_retry_window_passing_while_held_makes_a_failed_fallback(
    announcement_app, client, doubles, clock
):
    from app.services.announcements import TransportError

    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    cast.play_error = TransportError("connection_refused", certain_not_played=True)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    cast.play_error = None
    cast.speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    at(clock, "2027-01-15T12:45:00+00:00")
    service.tick()
    occurrence = only_occurrence(client, saved["id"])["occurrence"]
    assert occurrence["status"] == "closed"
    assert occurrence["disposition"] == "missed"
    assert occurrence["disposition_reason"] == "music_playing"
    assert occurrence["fallback_reason"] == "failed"
    assert len(cast.plays) == 1
