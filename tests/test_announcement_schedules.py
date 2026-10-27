"""One-time reminder schedules and their occurrences through public operations.

Real Controller, isolated SQLite, real service policy; doubles only for the
Piper subprocess and the Cast transport. ``START`` is 2027-01-15 12:00 UTC,
i.e. 14:00 in Europe/Helsinki (UTC+2 in winter).
"""

from __future__ import annotations

import contextlib

import announcement_support as support
from announcement_support import headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord

API = "/api/announcements/schedules"


def save(app, client, expect=201, **body):
    payload = {
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "due_local": "2027-01-15T14:30",
        **body,
    }
    response = client.post(API, json=payload, headers=headers(app, client))
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def test_one_time_schedule_is_saved_with_its_helsinki_due_time(announcement_app, client):
    saved = save(announcement_app, client, request_id="schedule-request-1")
    schedule = saved["schedule"]
    assert schedule["kind"] == "one_time"
    assert schedule["status"] == "active"
    assert schedule["due_local"] == "2027-01-15T14:30"
    assert schedule["due_at"] == "2027-01-15T12:30:00+00:00"
    assert schedule["next_due_at"] == "2027-01-15T12:30:00+00:00"
    assert schedule["language"] == "fi"
    assert schedule["expiry_minutes"] == 15
    assert schedule["urgent"] is False
    assert schedule["quiet_hours_override"] is False
    assert saved["upcoming"] == [{"due_at": "2027-01-15T12:30:00+00:00"}]

    listed = client.get(API).get_json()["schedules"]
    assert [s["schedule"]["id"] for s in listed] == [schedule["id"]]
    assert listed[0]["upcoming"] == [{"due_at": "2027-01-15T12:30:00+00:00"}]

    again = client.post(
        API,
        json={
            "text": "Muista ottaa lääkkeet.",
            "target": "jbl-right",
            "due_local": "2027-01-15T14:30",
            "request_id": "schedule-request-1",
        },
        headers=headers(announcement_app, client),
    )
    assert again.status_code == 200
    assert again.get_json()["schedule"]["id"] == schedule["id"]
    assert len(client.get(API).get_json()["schedules"]) == 1


def history(client):
    return client.get("/api/announcements/occurrences").get_json()["occurrences"]


def at(clock, iso_utc):
    from datetime import datetime

    clock[0] = datetime.fromisoformat(iso_utc).timestamp()


def test_due_reminder_is_announced_once_and_history_keeps_the_evidence(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, volume_percent=50)
    at(clock, "2027-01-15T12:29:59+00:00")
    service.tick()
    assert history(client) == []
    assert cast.effects == []

    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    service.tick()  # repeated ticks never duplicate the occurrence or the dispatch
    items = history(client)
    assert len(items) == 1
    occurrence = items[0]["occurrence"]
    attempts = items[0]["attempts"]
    assert occurrence["schedule_id"] == saved["schedule"]["id"]
    assert occurrence["instance_due_at"] == "2027-01-15T12:30:00+00:00"
    assert occurrence["expires_at"] == "2027-01-15T12:45:00+00:00"
    assert occurrence["status"] == "announcing"
    assert occurrence["delivery"] == "none"
    assert occurrence["quiet_hours"] == "22:00-07:00"
    assert [a["status"] for a in attempts] == ["dispatched"]
    assert attempts[0]["intent_at"] == "2027-01-15T12:30:00+00:00"
    assert attempts[0]["dispatch_deadline"] == "2027-01-15T12:45:00+00:00"
    assert attempts[0]["speaker_test_id"] is None
    assert [e[0] for e in cast.effects] == ["set_volume", "play"]
    assert items[0]["acknowledgement"] is None

    cast.report("192.168.1.119", attempts[0]["id"], "playing")
    clock[0] += 2
    service.tick()
    cast.report("192.168.1.119", attempts[0]["id"], "finished")
    clock[0] += 3
    service.tick()
    done = client.get(f"/api/announcements/occurrences/{occurrence['id']}").get_json()
    assert done["occurrence"]["status"] == "closed"
    assert done["occurrence"]["delivery"] == "delivered"
    assert done["occurrence"]["disposition"] is None
    assert done["occurrence"]["fallback_reason"] is None
    assert done["attempts"][0]["status"] == "finished"
    assert done["attempts"][0]["volume_restore"] == "restored"
    assert done["acknowledgement"] is None  # playback is not acknowledgement
    assert cast.speakers["192.168.1.119"].volume == 0.4
    schedule = client.get(f"{API}/{saved['schedule']['id']}").get_json()
    assert schedule["upcoming"] == []
    assert schedule["schedule"]["due_passed"] is True
    assert [o["occurrence"]["id"] for o in schedule["occurrences"]] == [occurrence["id"]]
    clock[0] += 3600
    service.tick()
    assert len(cast.plays) == 1


def rejected(app, client, field, **body):
    response = client.post(
        API,
        json={"text": "Mittaa verenpaine.", "target": "jbl-left", **body},
        headers=headers(app, client),
    )
    assert response.status_code == 400, response.get_json()
    data = response.get_json()
    assert data["field"] == field, data
    return data["message"]


def test_due_times_are_helsinki_wall_time_with_dst_choices(announcement_app, client, clock):
    at(clock, "2027-03-01T10:00:00+00:00")
    message = rejected(announcement_app, client, "due_local", due_local="2027-03-28T03:30")
    assert "does not exist" in message
    message = rejected(announcement_app, client, "fold", due_local="2027-10-31T03:30")
    assert "occurs twice" in message
    rejected(announcement_app, client, "fold", due_local="2027-10-31T03:30", fold=2)
    first = save(announcement_app, client, due_local="2027-10-31T03:30", fold=0)["schedule"]
    second = save(announcement_app, client, due_local="2027-10-31T03:30", fold=1)["schedule"]
    assert first["due_at"] == "2027-10-31T00:30:00+00:00"  # EEST, UTC+3
    assert second["due_at"] == "2027-10-31T01:30:00+00:00"  # EET, UTC+2
    assert (first["due_fold"], second["due_fold"]) == (0, 1)
    summer = save(announcement_app, client, due_local="2027-07-01T09:00")["schedule"]
    assert summer["due_at"] == "2027-07-01T06:00:00+00:00"
    assert summer["due_fold"] is None
    # An offset would let the browser's own timezone change the meaning.
    rejected(announcement_app, client, "due_local", due_local="2027-07-01T09:00+03:00")
    rejected(announcement_app, client, "due_local", due_local="2027-03-01T11:59")  # in the past
    rejected(announcement_app, client, "due_local", due_local=None)
    rejected(
        announcement_app, client, "expiry_minutes", due_local="2027-07-01T09:00", expiry_minutes=0
    )
    rejected(
        announcement_app, client, "expiry_minutes", due_local="2027-07-01T09:00", expiry_minutes=2.5
    )
    rejected(announcement_app, client, "urgent", due_local="2027-07-01T09:00", urgent="yes")
    rejected(announcement_app, client, "kind", due_local="2027-07-01T09:00", kind="daily")
    rejected(announcement_app, client, "text", due_local="2027-07-01T09:00", text="x" * 501)
    rejected(announcement_app, client, "target", due_local="2027-07-01T09:00", target="kitchen")
    assert len(client.get(API).get_json()["schedules"]) == 3


def occurrence_of(client, schedule_id):
    items = client.get(f"{API}/{schedule_id}").get_json()["occurrences"]
    assert len(items) == 1
    return items[0]


def test_quiet_hours_hold_speech_and_expiry_still_runs(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    # Helsinki 23:50 is held; it expires at 00:05 and must not speak at 07:00.
    late = save(announcement_app, client, due_local="2027-01-15T23:50")["schedule"]
    at(clock, "2027-01-15T21:50:00+00:00")
    service.tick()
    held = occurrence_of(client, late["id"])["occurrence"]
    assert held["status"] == "waiting"
    assert held["waiting_reason"] == "quiet_hours"
    at(clock, "2027-01-15T22:04:59+00:00")
    service.tick()
    assert occurrence_of(client, late["id"])["occurrence"]["status"] == "waiting"
    at(clock, "2027-01-15T22:05:00+00:00")  # exactly the expiry instant
    service.tick()
    missed = occurrence_of(client, late["id"])
    assert missed["occurrence"]["status"] == "closed"
    assert missed["occurrence"]["disposition"] == "missed"
    assert missed["occurrence"]["disposition_reason"] == "quiet_hours"
    assert missed["occurrence"]["delivery"] == "none"
    assert missed["occurrence"]["fallback_reason"] == "expired_undelivered"
    assert missed["occurrence"]["fallback_eligible_at"] == "2027-01-15T22:05:00+00:00"
    assert missed["occurrence"]["waiting_history"] == [
        {"reason": "quiet_hours", "since": "2027-01-15T21:50:00+00:00"}
    ]
    assert missed["attempts"] == []
    at(clock, "2027-01-16T05:00:00+00:00")  # 07:00 Helsinki
    service.tick()
    assert cast.plays == []


def test_quiet_hours_boundaries_and_the_separate_override(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    at(clock, "2027-01-15T12:00:00+00:00")
    before = save(announcement_app, client, due_local="2027-01-15T21:59")["schedule"]
    at(clock, "2027-01-15T19:59:00+00:00")
    service.tick()
    assert occurrence_of(client, before["id"])["occurrence"]["status"] == "announcing"
    cast.report("192.168.1.119", cast.plays[-1]["attempt_id"], "finished")
    service.tick()

    at(clock, "2027-01-15T19:59:30+00:00")
    start = save(announcement_app, client, due_local="2027-01-15T22:00", target="jbl-left")
    urgent = save(
        announcement_app, client, due_local="2027-01-15T22:00", urgent=True, text="Kiireellinen"
    )
    override = save(
        announcement_app,
        client,
        due_local="2027-01-15T22:00",
        quiet_hours_override=True,
        text="Yölläkin",
        target="jbl-left",
        request_id="override-request",
    )
    at(clock, "2027-01-15T20:00:00+00:00")
    service.tick()
    assert occurrence_of(client, start["schedule"]["id"])["occurrence"]["waiting_reason"] == (
        "quiet_hours"
    )
    # Urgency may interrupt music but is not permission to speak at night.
    assert occurrence_of(client, urgent["schedule"]["id"])["occurrence"]["waiting_reason"] == (
        "quiet_hours"
    )
    overridden = occurrence_of(client, override["schedule"]["id"])
    assert overridden["occurrence"]["quiet_hours"] is None
    assert overridden["occurrence"]["status"] == "announcing"
    assert [p["target"] for p in cast.plays] == ["jbl-right", "jbl-left"]

    at(clock, "2027-01-16T04:50:00+00:00")
    morning = save(announcement_app, client, due_local="2027-01-16T06:55", text="Aamu")["schedule"]
    at(clock, "2027-01-16T04:55:00+00:00")
    service.tick()
    assert occurrence_of(client, morning["id"])["occurrence"]["waiting_reason"] == "quiet_hours"
    at(clock, "2027-01-16T04:59:59+00:00")
    service.tick()
    assert occurrence_of(client, morning["id"])["occurrence"]["status"] == "waiting"
    at(clock, "2027-01-16T05:00:00+00:00")  # 07:00 Helsinki: quiet hours end exclusive
    service.tick()
    spoken = occurrence_of(client, morning["id"])
    assert spoken["occurrence"]["status"] == "announcing"
    assert spoken["attempts"][0]["intent_at"] == "2027-01-16T05:00:00+00:00"
    assert spoken["attempts"][0]["dispatch_deadline"] == "2027-01-16T05:10:00+00:00"


def test_ordinary_speech_waits_for_music_and_a_track_change_is_not_idle(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    speaker = cast.speakers["192.168.1.119"]
    saved = save(announcement_app, client)["schedule"]
    speaker.state = "playing"  # Spotify on Jbl-Right
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    waiting = occurrence_of(client, saved["id"])["occurrence"]
    assert waiting["status"] == "waiting"
    assert waiting["waiting_reason"] == "music_playing"

    speaker.state = "unknown"  # e.g. Spotify paused or app state unclear
    clock[0] += 5
    service.tick()
    assert occurrence_of(client, saved["id"])["occurrence"]["waiting_reason"] == "media_unknown"

    speaker.state = "idle"  # gap between tracks
    clock[0] += 5
    service.tick()
    speaker.state = "playing"
    clock[0] += 5
    service.tick()
    assert cast.plays == []

    speaker.state = "idle"  # music really stopped
    clock[0] += 5
    service.tick()
    assert cast.plays == []  # idle for 5 s is still within the debounce window
    clock[0] += 5
    service.tick()
    spoken = occurrence_of(client, saved["id"])
    assert spoken["occurrence"]["status"] == "announcing"
    assert spoken["attempts"][0]["interrupted_media"] is None
    assert [h["reason"] for h in spoken["occurrence"]["waiting_history"]] == [
        "music_playing",
        "media_unknown",
        "music_playing",
    ]


def test_music_until_expiry_means_missed_not_late_speech(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers["192.168.1.119"].state = "playing"
    saved = save(announcement_app, client, expiry_minutes=5)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    for _ in range(80):  # five minutes of ticks while music keeps playing
        service.tick()
        clock[0] += 4
    cast.speakers["192.168.1.119"].state = "idle"
    clock[0] += 60
    service.tick()
    missed = occurrence_of(client, saved["id"])["occurrence"]
    assert missed["disposition"] == "missed"
    assert missed["disposition_reason"] == "music_playing"
    assert missed["fallback_reason"] == "expired_undelivered"
    assert missed["closed_at"] == "2027-01-15T12:35:00+00:00"
    assert cast.plays == []


def test_urgent_reminder_may_interrupt_music_and_restoration_is_not_promised(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    status = client.get("/api/announcements/status").get_json()
    assert status["music_restoration"] == {"available": False, "reason": "not_configured"}
    assert status["schedules"]["quiet_hours"] == "22:00-07:00"
    assert status["schedules"]["default_expiry_minutes"] == 15
    cast.speakers["192.168.1.119"].state = "playing"
    cast.speakers["192.168.1.119"].volume = 0.7
    saved = save(announcement_app, client, urgent=True, volume_percent=40)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    item = occurrence_of(client, saved["id"])
    attempt = item["attempts"][0]
    assert item["occurrence"]["status"] == "announcing"
    assert attempt["priority"] == 1
    assert attempt["interrupted_media"] == "playing"
    assert attempt["media_restore"] == "unavailable"
    assert attempt["volume_before"] == 0.7
    cast.report("192.168.1.119", attempt["id"], "finished")
    clock[0] += 5
    service.tick()
    finished = occurrence_of(client, saved["id"])
    assert finished["occurrence"]["delivery"] == "delivered"
    assert finished["attempts"][0]["volume_restore"] == "restored"
    assert cast.speakers["192.168.1.119"].volume == 0.7
    assert finished["attempts"][0]["media_restore"] == "unavailable"


def test_urgent_waiting_work_goes_first_without_cutting_off_current_speech(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    ordinary = save(announcement_app, client, text="Tavallinen")["schedule"]
    urgent = save(announcement_app, client, text="Kiireellinen", urgent=True)["schedule"]
    at(clock, "2027-01-15T12:29:50+00:00")
    test = client.post(
        "/api/announcements/tests",
        json={"text": "Testi", "target": "jbl-right"},
        headers=headers(announcement_app, client),
    ).get_json()
    service.tick()
    cast.report("192.168.1.119", test["attempt"]["id"], "playing")
    service.tick()  # the speaker test is now speaking on Jbl-Right
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    for schedule in (ordinary, urgent):
        occurrence = occurrence_of(client, schedule["id"])["occurrence"]
        assert occurrence["waiting_reason"] == "speaker_busy"
    assert len(cast.plays) == 1
    assert ("stop", "jbl-right", test["attempt"]["id"]) not in cast.effects

    cast.report("192.168.1.119", test["attempt"]["id"], "finished")
    clock[0] += 5
    service.tick()
    assert [p["attempt_id"] for p in cast.plays][1:] == [
        occurrence_of(client, urgent["id"])["attempts"][0]["id"]
    ]
    assert occurrence_of(client, ordinary["id"])["occurrence"]["waiting_reason"] == "speaker_busy"
    cast.report("192.168.1.119", cast.plays[-1]["attempt_id"], "finished")
    clock[0] += 5
    service.tick()
    assert (
        cast.plays[-1]["attempt_id"] == occurrence_of(client, ordinary["id"])["attempts"][0]["id"]
    )
    # Queueing never moved the ordinary reminder's expiry.
    assert occurrence_of(client, ordinary["id"])["occurrence"]["expires_at"] == (
        "2027-01-15T12:45:00+00:00"
    )


def put(app, client, schedule_id, expect=200, **body):
    payload = {
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "due_local": "2027-01-15T14:30",
        **body,
    }
    response = client.put(f"{API}/{schedule_id}", json=payload, headers=headers(app, client))
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def act(app, client, schedule_id, action, expect=200):
    if action == "delete":
        response = client.delete(f"{API}/{schedule_id}", headers=headers(app, client))
    else:
        response = client.post(f"{API}/{schedule_id}/{action}", headers=headers(app, client))
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def test_edits_affect_future_instances_and_keep_occurrence_snapshots(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    edited = put(announcement_app, client, saved["id"], due_local="2027-01-15T14:40", revision=1)
    assert edited["schedule"]["revision"] == 2
    assert edited["upcoming"] == [{"due_at": "2027-01-15T12:40:00+00:00"}]
    same = put(announcement_app, client, saved["id"], due_local="2027-01-15T14:40", revision=2)
    assert same["schedule"]["revision"] == 2  # an identical save is not a new revision
    put(announcement_app, client, saved["id"], expect=409, due_local="2027-01-15T14:45", revision=1)

    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    assert cast.plays == []  # the old time no longer applies
    at(clock, "2027-01-15T12:40:00+00:00")
    cast.speakers["192.168.1.119"].state = "playing"
    service.tick()
    put(
        announcement_app,
        client,
        saved["id"],
        due_local="2027-01-15T15:00",
        text="Uusi teksti",
        language="en",
    )
    occurrence = occurrence_of(client, saved["id"])["occurrence"]
    assert occurrence["text"] == "Muista ottaa lääkkeet."  # history keeps what was due
    assert occurrence["language"] == "fi"
    assert occurrence["schedule_revision"] == 2
    detail = client.get(f"{API}/{saved['id']}").get_json()
    assert detail["schedule"]["revision"] == 3
    assert detail["upcoming"] == [{"due_at": "2027-01-15T13:00:00+00:00"}]


def test_pause_cancels_pending_speech_and_resume_does_not_revive_it(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    cast.speakers["192.168.1.119"].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    paused = act(announcement_app, client, saved["id"], "pause")
    assert paused["schedule"]["status"] == "paused"
    assert paused["upcoming"] == []
    again = act(announcement_app, client, saved["id"], "pause")  # repeated request
    assert again["schedule"]["status"] == "paused"
    cancelled = occurrence_of(client, saved["id"])["occurrence"]
    assert cancelled["status"] == "closed"
    assert cancelled["disposition"] == "cancelled"
    assert cancelled["disposition_reason"] == "schedule_paused"
    assert cancelled["fallback_reason"] is None  # cancellation is not a delivery failure

    cast.speakers["192.168.1.119"].state = "idle"
    clock[0] += 60
    service.tick()
    resumed = act(announcement_app, client, saved["id"], "resume")
    assert resumed["schedule"]["status"] == "active"
    assert resumed["upcoming"] == []
    assert resumed["schedule"]["due_passed"] is True
    clock[0] += 60
    service.tick()
    assert cast.plays == []
    assert occurrence_of(client, saved["id"])["occurrence"]["disposition"] == "cancelled"


def test_pause_before_due_skips_the_instance_and_resume_before_due_restores_it(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    act(announcement_app, client, saved["id"], "pause")
    at(clock, "2027-01-15T12:20:00+00:00")
    service.tick()
    resumed = act(announcement_app, client, saved["id"], "resume")
    assert resumed["upcoming"] == [{"due_at": "2027-01-15T12:30:00+00:00"}]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    assert len(cast.plays) == 1


def test_delete_stops_future_delivery_but_keeps_history(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    played = save(announcement_app, client)["schedule"]
    future = save(announcement_app, client, due_local="2027-01-15T16:00", text="Myöhemmin")
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    attempt_id = cast.plays[0]["attempt_id"]
    deleted = act(announcement_app, client, played["id"], "delete")
    assert deleted["schedule"]["status"] == "deleted"
    act(announcement_app, client, played["id"], "delete")  # repeated delete is harmless
    act(announcement_app, client, future["schedule"]["id"], "delete")
    assert client.get(API).get_json()["schedules"] == []
    # Current speech is not cut off; it finishes and its evidence is kept.
    assert ("stop", "jbl-right", attempt_id) not in cast.effects
    cast.report("192.168.1.119", attempt_id, "finished")
    clock[0] += 5
    service.tick()
    kept = history(client)
    assert len(kept) == 1
    assert kept[0]["occurrence"]["delivery"] == "delivered"
    assert kept[0]["occurrence"]["disposition"] == "cancelled"
    assert kept[0]["occurrence"]["disposition_reason"] == "schedule_deleted"
    assert kept[0]["occurrence"]["fallback_reason"] is None
    at(clock, "2027-01-15T14:00:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    put(announcement_app, client, played["id"], expect=409, due_local="2027-01-15T17:00")
    act(announcement_app, client, played["id"], "resume", expect=409)
    act(announcement_app, client, "missing-id", "pause", expect=404)


def test_pause_during_dispatch_preparation_wins_and_nothing_plays(
    announcement_app, client, doubles, clock
):
    synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    synth.during = lambda: act(announcement_app, client, saved["id"], "pause")
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["disposition"] == "cancelled"
    assert item["attempts"] == []
    assert cast.effects == []
    act(announcement_app, client, saved["id"], "resume")
    clock[0] += 30
    service.tick()
    assert cast.effects == []


def test_expiry_reached_during_preparation_is_not_spoken_late(
    announcement_app, client, doubles, clock
):
    synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, expiry_minutes=1)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    synth.during = lambda: clock.__setitem__(0, clock[0] + 61)  # very slow synthesis
    service.tick()
    assert cast.effects == []
    service.tick()
    missed = occurrence_of(client, saved["id"])
    assert missed["occurrence"]["disposition"] == "missed"
    assert missed["occurrence"]["disposition_reason"] == "expired_before_dispatch"
    assert missed["attempts"] == []


def test_recovered_speaker_is_used_within_the_original_window(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers["192.168.1.119"].state = "unavailable"
    saved = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    for _ in range(3):
        service.tick()
        clock[0] += 1  # retries within the probe interval do not hammer the speaker
    cast.speakers["192.168.1.119"].state = "idle"
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["status"] == "announcing"
    assert item["occurrence"]["expires_at"] == "2027-01-15T12:45:00+00:00"
    assert item["attempts"][0]["dispatch_deadline"] == "2027-01-15T12:45:00+00:00"
    assert len(item["attempts"]) == 1


def test_definitive_and_uncertain_failures_make_the_occurrence_fallback_eligible(
    announcement_app, client, doubles, clock
):
    from app.services.announcements import TransportError

    synth, cast = doubles
    service = announcement_app.announcement_service
    voice = save(announcement_app, client, text="Ei ääntä")["schedule"]
    synth.failure = "synthesis_failed"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    failed = occurrence_of(client, voice["id"])
    assert failed["occurrence"]["delivery"] == "failed"
    assert failed["occurrence"]["delivery_reason"] == "synthesis_failed"
    assert failed["occurrence"]["fallback_reason"] == "failed"
    assert [(a["status"], a["reason"], a["intent_at"]) for a in failed["attempts"]] == [
        ("failed", "synthesis_failed", None)
    ]
    synth.failure = None

    uncertain = save(announcement_app, client, due_local="2027-01-15T14:50", text="Epävarma")
    cast.play_error = TransportError("load_uncertain", certain_not_played=False)
    at(clock, "2027-01-15T12:50:00+00:00")
    service.tick()
    unknown = occurrence_of(client, uncertain["schedule"]["id"])
    assert unknown["occurrence"]["delivery"] == "unknown"
    assert unknown["occurrence"]["fallback_reason"] == "unknown"
    assert unknown["attempts"][0]["status"] == "unknown"
    cast.play_error = None

    silent = save(announcement_app, client, due_local="2027-01-15T15:00", text="Hiljaa")
    at(clock, "2027-01-15T13:00:00+00:00")
    service.tick()
    clock[0] += 31
    service.tick()
    no_evidence = occurrence_of(client, silent["schedule"]["id"])["occurrence"]
    assert no_evidence["delivery"] == "unknown"
    assert no_evidence["delivery_reason"] == "no_playback_evidence"
    assert no_evidence["fallback_reason"] == "unknown"
    plays = len(cast.plays)
    clock[0] += 600
    service.tick()
    assert len(cast.plays) == plays  # nothing is retried automatically


def test_cast_unavailable_or_missing_media_origin_fail_visibly(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    announcement_app.announcement_service = support.build_service(
        announcement_app.ctrl, clock, doubles, tmp_path / "cache", media_origin=None
    )
    saved = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    occurrence = occurrence_of(client, saved["id"])["occurrence"]
    assert occurrence["delivery_reason"] == "media_origin_not_configured"
    assert occurrence["fallback_reason"] == "failed"
    assert cast.effects == []


def restart(app, clock, doubles, tmp_path):
    service = support.build_service(app.ctrl, clock, doubles, tmp_path / "cache")
    app.announcement_service = service
    return service


def test_restart_marks_in_flight_speech_unknown_and_never_replays_it(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    saved = save(announcement_app, client)["schedule"]
    cast.play_crash = True
    at(clock, "2027-01-15T12:30:00+00:00")
    with contextlib.suppress(support.SimulatedCrash):
        announcement_app.announcement_service.tick()
    cast.play_crash = False
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["status"] == "announcing"
    assert item["attempts"][0]["status"] == "dispatching"  # intent was durable first

    clock[0] += 20
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    service.tick()
    recovered = occurrence_of(client, saved["id"])
    assert recovered["occurrence"]["status"] == "closed"
    assert recovered["occurrence"]["delivery"] == "unknown"
    assert recovered["occurrence"]["delivery_reason"] == "interrupted_by_restart"
    assert recovered["occurrence"]["fallback_reason"] == "unknown"
    assert recovered["attempts"][0]["status"] == "unknown"
    assert len(cast.plays) == 1


def test_crash_before_dispatch_intent_is_retried_once_within_the_window(
    announcement_app, client, doubles, clock, tmp_path
):
    synth, cast = doubles
    saved = save(announcement_app, client)["schedule"]

    def crash():
        raise support.SimulatedCrash()

    synth.during = crash
    at(clock, "2027-01-15T12:30:00+00:00")
    with contextlib.suppress(support.SimulatedCrash):
        announcement_app.announcement_service.tick()
    assert occurrence_of(client, saved["id"])["attempts"] == []
    at(clock, "2027-01-15T12:35:00+00:00")
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    service.tick()
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["status"] == "announcing"
    assert len(item["attempts"]) == 1
    assert len(cast.plays) == 1


def test_downtime_past_expiry_records_a_missed_occurrence_without_speech(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    saved = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T13:10:00+00:00")  # the server was down from 12:00 to 13:10
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    service.tick()
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["instance_due_at"] == "2027-01-15T12:30:00+00:00"
    assert item["occurrence"]["disposition"] == "missed"
    assert item["occurrence"]["disposition_reason"] == "expired_before_dispatch"
    assert item["occurrence"]["fallback_reason"] == "expired_undelivered"
    assert item["attempts"] == []
    assert cast.plays == []


def test_waiting_reminder_survives_restart_and_keeps_its_expiry(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    cast.speakers["192.168.1.119"].state = "playing"
    saved = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    at(clock, "2027-01-15T12:40:00+00:00")
    service = restart(announcement_app, clock, doubles, tmp_path)
    cast.speakers["192.168.1.119"].state = "idle"
    service.tick()
    item = occurrence_of(client, saved["id"])
    assert item["occurrence"]["status"] == "announcing"
    assert item["occurrence"]["expires_at"] == "2027-01-15T12:45:00+00:00"
    assert len(cast.plays) == 1


def test_root_admin_and_csrf_guard_schedule_operations(announcement_app, client):
    saved = save(announcement_app, client)["schedule"]
    sid = saved["id"]
    reads = [API, f"{API}/{sid}", "/api/announcements/occurrences"]
    body = {"text": "Hei", "target": "jbl-left", "due_local": "2027-01-15T15:00"}
    writes = [
        ("post", API, body),
        ("put", f"{API}/{sid}", body),
        ("post", f"{API}/{sid}/pause", {}),
        ("post", f"{API}/{sid}/resume", {}),
        ("delete", f"{API}/{sid}", None),
    ]
    anonymous = announcement_app.test_client()
    admin = support.login(announcement_app.test_client(), "admin")
    for url in reads:
        assert anonymous.get(url).status_code == 401
        assert admin.get(url).status_code == 403
    for method, url, payload in writes:
        assert getattr(anonymous, method)(url, json=payload).status_code in (400, 401)
        auth = headers(announcement_app, admin)
        assert getattr(admin, method)(url, json=payload, headers=auth).status_code == 403
        assert getattr(client, method)(url, json=payload).status_code == 400  # no CSRF token
    detail = client.get(f"{API}/{sid}").get_json()
    assert detail["schedule"]["status"] == "active"
    assert detail["schedule"]["revision"] == 1
    assert len(client.get(API).get_json()["schedules"]) == 1


def test_reminder_controls_are_on_the_announcement_page(announcement_app, client):
    html = client.get("/settings/announcements").get_data(as_text=True)
    for element in (
        'id="announcementScheduleButton"',
        'id="announceDate"',
        'id="announceTime"',
        'id="announceAdvanced"',
        'id="announceSchedulesList"',
        'id="announceHistoryList"',
    ):
        assert element in html
    assert "js/announcements.js?v=20261027-7" in html
    assert "css/announcements.css?v=20261027-7" in html
