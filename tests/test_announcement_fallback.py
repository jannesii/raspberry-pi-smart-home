"""Discord fallback notifications for reminder occurrences, through public operations.

Real Controller, isolated SQLite, real service policy and the real webhook
classification; doubles only for Piper, Cast and Discord's HTTP endpoint
(``FakeDiscord.post``). No webhook is contacted. ``START`` is 2027-01-15 12:00
UTC (14:00 Helsinki).
"""

from __future__ import annotations

from datetime import datetime

import announcement_support as support
from announcement_support import WEBHOOK_URL, headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord

API = "/api/announcements"


def at(clock, iso_utc):
    clock[0] = datetime.fromisoformat(iso_utc).timestamp()


def save(app, client, **body):
    payload = {
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "due_local": "2027-01-15T14:30",
        **body,
    }
    response = client.post(f"{API}/schedules", json=payload, headers=headers(app, client))
    assert response.status_code == 201, response.get_json()
    return response.get_json()["schedule"]


def item_of(client, schedule_id):
    detail = client.get(f"{API}/schedules/{schedule_id}").get_json()
    (item,) = detail["occurrences"]
    return client.get(f"{API}/occurrences/{item['occurrence']['id']}").get_json()


def test_failed_speech_sends_one_discord_fallback_with_the_reminder_text(
    announcement_app, client, doubles, discord, clock
):
    synth, _cast = doubles
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)
    synth.failure = "synthesis_failed"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()

    item = item_of(client, schedule["id"])
    assert item["occurrence"]["fallback_reason"] == "failed"
    fallback = item["fallback"]
    assert fallback["channel"] == "discord"
    assert fallback["reason"] == "failed"
    assert fallback["status"] == "accepted"
    assert fallback["accepted_at"] == "2027-01-15T12:30:00+00:00"
    assert fallback["attempts_made"] == 1
    assert fallback["max_attempts"] == 3
    assert [(a["number"], a["status"], a["http_status"]) for a in fallback["attempts"]] == [
        (1, "accepted", 200)
    ]
    assert item["acknowledgement"] is None  # Discord acceptance is not acknowledgement

    (request,) = discord.requests
    assert request["url"] == WEBHOOK_URL
    assert request["json"]["allowed_mentions"] == {"parse": []}
    content = request["json"]["content"]
    assert "Muista ottaa lääkkeet." in content
    assert "Spoken delivery failed: speech generation failed." in content
    assert content.startswith(
        "**Reminder not spoken** · Jbl-Right · due 15.1.2027 14:30 (Helsinki)"
    )
    occurrence_id = item["occurrence"]["id"]
    assert f"https://home.test/settings/announcements?occurrence={occurrence_id}" in content

    clock[0] += 3600
    service.tick()
    service.tick()
    assert len(discord.requests) == 1
