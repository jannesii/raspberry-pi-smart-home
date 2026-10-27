"""One-time reminder schedules and their occurrences through public operations.

Real Controller, isolated SQLite, real service policy; doubles only for the
Piper subprocess and the Cast transport. ``START`` is 2027-01-15 12:00 UTC,
i.e. 14:00 in Europe/Helsinki (UTC+2 in winter).
"""

from __future__ import annotations

import announcement_support as support
from announcement_support import headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles

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
