"""Discord fallback notifications for reminder occurrences, through public operations.

Real Controller, isolated SQLite, real service policy and the real webhook
classification; doubles only for Piper, Cast and Discord's HTTP endpoint
(``FakeDiscord.post``). No webhook is contacted. ``START`` is 2027-01-15 12:00
UTC (14:00 Helsinki).
"""

from __future__ import annotations

import contextlib
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


def fail_speech(app, client, doubles, clock, **body):
    """Save a reminder whose speech fails definitively at its due time."""
    synth, _cast = doubles
    schedule = save(app, client, **body)
    synth.failure = "synthesis_failed"
    at(clock, "2027-01-15T12:30:00+00:00")
    app.announcement_service.tick()
    synth.failure = None
    return schedule


def test_confirmed_failures_get_three_attempts_respecting_retry_delays(
    announcement_app, client, doubles, discord, clock
):
    service = announcement_app.announcement_service
    discord.outcomes = [
        support.DiscordResponse(429, {"retry_after": 90.0}),
        support.DiscordResponse(503),
        support.DiscordResponse(502),
    ]
    schedule = fail_speech(announcement_app, client, doubles, clock)
    fallback = item_of(client, schedule["id"])["fallback"]
    assert fallback["status"] == "pending"
    assert fallback["status_reason"] == "rate_limited"
    # The server asked for 90 s, longer than the first 60 s backoff.
    assert fallback["next_attempt_at"] == "2027-01-15T12:31:30+00:00"

    at(clock, "2027-01-15T12:31:29+00:00")
    service.tick()
    assert len(discord.requests) == 1
    at(clock, "2027-01-15T12:31:30+00:00")
    service.tick()
    fallback = item_of(client, schedule["id"])["fallback"]
    assert len(discord.requests) == 2
    assert fallback["next_attempt_at"] == "2027-01-15T12:36:30+00:00"

    at(clock, "2027-01-15T12:36:30+00:00")
    service.tick()
    item = item_of(client, schedule["id"])
    fallback = item["fallback"]
    assert fallback["status"] == "failed"
    assert fallback["status_reason"] == "attempts_exhausted"
    assert fallback["attempts_made"] == 3
    assert [(a["status"], a["http_status"], a["retry_after_s"]) for a in fallback["attempts"]] == [
        ("rate_limited", 429, 90.0),
        ("rejected", 503, None),
        ("rejected", 502, None),
    ]
    assert len(set(discord.messages)) == 1  # every retry sends the same message
    # Speech expiry is not touched by fallback retries.
    assert item["occurrence"]["expires_at"] == "2027-01-15T12:45:00+00:00"

    clock[0] += 86400
    service.tick()
    assert len(discord.requests) == 3


def test_timeout_after_sending_is_unknown_and_never_resent(
    announcement_app, client, doubles, discord, clock
):
    import requests

    discord.outcomes = [requests.ReadTimeout("read timed out")]
    schedule = fail_speech(announcement_app, client, doubles, clock)
    for minutes in (1, 10, 600):
        clock[0] += minutes * 60
        announcement_app.announcement_service.tick()
    fallback = item_of(client, schedule["id"])["fallback"]
    assert fallback["status"] == "unknown"
    assert fallback["status_reason"] == "acceptance_unknown"
    assert fallback["accepted_at"] is None
    assert [(a["status"], a["detail"]) for a in fallback["attempts"]] == [
        ("unknown", "ReadTimeout")
    ]
    assert len(discord.requests) == 1


def test_a_request_that_never_left_is_a_confirmed_failure_and_retried(
    announcement_app, client, doubles, discord, clock
):
    import requests

    discord.outcomes = [requests.ConnectTimeout("connect timed out")]
    schedule = fail_speech(announcement_app, client, doubles, clock)
    at(clock, "2027-01-15T12:31:00+00:00")
    announcement_app.announcement_service.tick()
    fallback = item_of(client, schedule["id"])["fallback"]
    assert [a["status"] for a in fallback["attempts"]] == ["failed", "accepted"]
    assert fallback["status"] == "accepted"
    assert fallback["accepted_at"] == "2027-01-15T12:31:00+00:00"


def test_permanent_rejection_is_not_retried(announcement_app, client, doubles, discord, clock):
    discord.outcomes = [support.DiscordResponse(404, {"message": "Unknown Webhook"})]
    schedule = fail_speech(announcement_app, client, doubles, clock)
    clock[0] += 3600
    announcement_app.announcement_service.tick()
    fallback = item_of(client, schedule["id"])["fallback"]
    assert (fallback["status"], fallback["status_reason"]) == ("failed", "rejected")
    assert len(discord.requests) == 1


def test_missing_webhook_configuration_is_visible_in_history_and_status(
    announcement_app, client, doubles, discord, clock, tmp_path
):
    announcement_app.announcement_service = support.build_service(
        announcement_app.ctrl, clock, doubles, tmp_path / "cache", discord=discord, webhook_url=None
    )
    status = client.get(f"{API}/status").get_json()["fallback"]
    assert status["channel"] == "discord"
    assert (status["available"], status["reason"]) == (False, "not_configured")
    assert status["last"] is None

    schedule = fail_speech(announcement_app, client, doubles, clock)
    item = item_of(client, schedule["id"])
    # Both channels failed; that stays observable.
    assert item["occurrence"]["delivery"] == "failed"
    fallback = item["fallback"]
    assert (fallback["status"], fallback["status_reason"]) == ("unavailable", "not_configured")
    assert [a["status"] for a in fallback["attempts"]] == ["not_configured"]
    assert discord.requests == []
    last = client.get(f"{API}/status").get_json()["fallback"]["last"]
    assert last["status"] == "unavailable"
    assert last["occurrence_id"] == item["occurrence"]["id"]


def restart(app, clock, doubles, discord, tmp_path):
    from app.core import Controller

    ctrl = Controller(db_path=app.config["DB_PATH"])
    ctrl._sa_engine = app.ctrl._sa_engine
    app.announcement_service = support.build_service(
        ctrl, clock, doubles, tmp_path / "cache", discord=discord
    )
    return app.announcement_service


def test_restart_during_a_request_records_unknown_and_never_resends(
    announcement_app, client, doubles, discord, clock, tmp_path
):
    def crash():
        raise support.SimulatedCrash()

    discord.during = crash  # the process dies while the request is in flight
    schedule = save(announcement_app, client)
    doubles[0].failure = "synthesis_failed"
    at(clock, "2027-01-15T12:30:00+00:00")
    with contextlib.suppress(support.SimulatedCrash):
        announcement_app.announcement_service.tick()
    sending = item_of(client, schedule["id"])["fallback"]
    assert sending["status"] == "sending"  # intent was durable before the request
    assert [a["status"] for a in sending["attempts"]] == ["sending"]

    clock[0] += 30
    service = restart(announcement_app, clock, doubles, discord, tmp_path)
    for _ in range(3):
        service.tick()
        clock[0] += 600
    fallback = item_of(client, schedule["id"])["fallback"]
    assert (fallback["status"], fallback["status_reason"]) == ("unknown", "interrupted_by_restart")
    assert [(a["status"], a["detail"]) for a in fallback["attempts"]] == [
        ("unknown", "interrupted_by_restart")
    ]
    assert len(discord.requests) == 1


def test_restarts_neither_reset_the_budget_nor_open_a_second_fallback(
    announcement_app, client, doubles, discord, clock, tmp_path
):
    discord.outcomes = [support.DiscordResponse(500)] * 5
    schedule = fail_speech(announcement_app, client, doubles, clock)
    at(clock, "2027-01-15T12:31:00+00:00")
    announcement_app.announcement_service.tick()
    assert len(discord.requests) == 2

    for _ in range(3):
        clock[0] += 3600
        service = restart(announcement_app, clock, doubles, discord, tmp_path)
        service.tick()
        service.tick()
    item = item_of(client, schedule["id"])
    assert item["fallback"]["status"] == "failed"
    assert item["fallback"]["attempts_made"] == 3
    assert len(discord.requests) == 3
    history = client.get(f"{API}/occurrences").get_json()["occurrences"]
    assert [h["fallback"]["id"] for h in history] == [item["fallback"]["id"]]


def test_quiet_hours_and_music_holds_until_expiry_send_a_labelled_missed_reminder(
    announcement_app, client, doubles, discord, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    night = save(announcement_app, client, due_local="2027-01-15T23:30", text="Yölääke")
    at(clock, "2027-01-15T21:30:00+00:00")
    service.tick()
    assert discord.requests == []  # waiting is not a fallback trigger
    at(clock, "2027-01-15T21:45:00+00:00")
    service.tick()
    item = item_of(client, night["id"])
    assert item["occurrence"]["disposition"] == "missed"
    assert item["fallback"]["reason"] == "expired_undelivered"
    assert item["fallback"]["status"] == "accepted"
    assert discord.messages[-1].split("\n")[:3] == [
        "**Missed reminder** · Jbl-Right · due 15.1.2027 23:30 (Helsinki)",
        "Yölääke",
        "Expired at 15.1.2027 23:45 without being spoken while waiting for quiet hours to end.",
    ]

    music = save(announcement_app, client, due_local="2027-01-16T09:00", text="Aamulääke")
    cast.speakers["192.168.1.119"].state = "playing"
    at(clock, "2027-01-16T07:00:00+00:00")
    service.tick()
    at(clock, "2027-01-16T07:15:00+00:00")
    service.tick()
    assert item_of(client, music["id"])["fallback"]["reason"] == "expired_undelivered"
    assert "while waiting for music to stop." in discord.messages[-1]
    assert cast.plays == []
    assert len(discord.requests) == 2


def test_downtime_past_expiry_sends_a_missed_reminder_after_restart(
    announcement_app, client, doubles, discord, clock, tmp_path
):
    schedule = save(announcement_app, client)
    at(clock, "2027-01-15T13:10:00+00:00")  # the server was down from 12:00 to 13:10
    service = restart(announcement_app, clock, doubles, discord, tmp_path)
    service.tick()
    service.tick()
    item = item_of(client, schedule["id"])
    assert item["fallback"]["reason"] == "expired_undelivered"
    assert item["fallback"]["created_at"] == "2027-01-15T13:10:00+00:00"
    (message,) = discord.messages
    assert message.startswith("**Missed reminder**")
    assert "could not be sent in time (for example the server was down)" in message


def test_uncertain_speech_sends_a_fallback_saying_it_may_have_played(
    announcement_app, client, doubles, discord, clock
):
    from app.services.announcements import TransportError

    _synth, cast = doubles
    cast.play_error = TransportError("load_uncertain", certain_not_played=False)
    schedule = save(announcement_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    assert item_of(client, schedule["id"])["fallback"]["reason"] == "unknown"
    (message,) = discord.messages
    assert message.startswith("**Reminder may not have been spoken**")
    assert "The speaker may have played it." in message
    assert len(cast.plays) == 1  # the uncertain speech itself is not replayed


def test_partial_delivery_evidence_sends_a_partial_fallback(
    announcement_app, client, doubles, discord, clock
):
    """Group targets (#37) record partial delivery; the fallback already handles it."""
    _synth, cast = doubles
    cast.speakers["192.168.1.119"].state = "unavailable"
    schedule = save(announcement_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service = announcement_app.announcement_service
    service.tick()
    (waiting,) = client.get(f"{API}/occurrences").get_json()["occurrences"]
    # Stand-in for #37's settle of group evidence: one member reported playback.
    assert announcement_app.ctrl.update_announcement_occurrence(
        waiting["occurrence"]["id"],
        expected=("waiting",),
        status="closed",
        delivery="partial",
        delivery_reason="speaker_unavailable",
        fallback_reason="partial",
        fallback_eligible_at="2027-01-15T12:31:00+00:00",
        closed_at="2027-01-15T12:31:00+00:00",
        updated_at="2027-01-15T12:31:00+00:00",
    )
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    assert item_of(client, schedule["id"])["fallback"]["reason"] == "partial"
    (message,) = discord.messages
    assert message.startswith("**Reminder only partly spoken**")
    assert "Only part of Jbl-Right reported playback: the speaker did not respond." in message


def test_delivered_cancelled_saved_or_previewed_reminders_send_nothing(
    announcement_app, client, doubles, discord, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    spoken = save(announcement_app, client)
    paused = save(announcement_app, client, due_local="2027-01-15T15:00", text="Tauotettu")
    preview = client.post(
        f"{API}/previews",
        json={"text": "Esikuuntelu"},
        headers=headers(announcement_app, client),
    )
    assert preview.status_code == 202
    service.tick()
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    (attempt,) = item_of(client, spoken["id"])["attempts"]
    cast.report("192.168.1.119", attempt["id"], "playing")
    clock[0] += 2
    service.tick()
    cast.report("192.168.1.119", attempt["id"], "finished")
    clock[0] += 2
    service.tick()

    # Pause while it waits for music: explicit cancellation is not a failure.
    cast.speakers["192.168.1.119"].state = "playing"
    at(clock, "2027-01-15T13:00:00+00:00")
    service.tick()
    response = client.post(
        f"{API}/schedules/{paused['id']}/pause", headers=headers(announcement_app, client)
    )
    assert response.status_code == 200
    clock[0] += 3600  # long after both would have expired, still unacknowledged
    service.tick()

    delivered = item_of(client, spoken["id"])
    assert delivered["occurrence"]["delivery"] == "delivered"
    assert delivered["acknowledgement"] is None
    assert delivered["fallback"] is None  # no escalation for lack of acknowledgement
    cancelled = item_of(client, paused["id"])
    assert cancelled["occurrence"]["disposition"] == "cancelled"
    assert cancelled["fallback"] is None
    assert discord.requests == []


def test_message_disables_mentions_and_carries_no_credentials_or_media_urls(
    announcement_app, client, doubles, discord, clock
):
    text = "@everyone <@123> <@&456> muista lääkkeet https://example.test"
    schedule = fail_speech(announcement_app, client, doubles, clock, text=text)
    (request,) = discord.requests
    assert request["json"]["allowed_mentions"] == {"parse": []}
    assert set(request["json"]) == {"content", "allowed_mentions"}
    assert request["params"] == {"wait": "true"}
    content = request["json"]["content"]
    assert text in content.split("\n")  # the owner's wording, unchanged
    for secret in ("webhook-secret", support.MEDIA_ORIGIN, "announcement-media", "token"):
        assert secret not in content
    fallback = item_of(client, schedule["id"])["fallback"]
    assert fallback["message"] == content
    assert fallback["link"].startswith("https://home.test/settings/announcements?occurrence=")


def test_without_a_public_origin_the_message_points_to_the_page_without_a_link(
    announcement_app, client, doubles, discord, clock, tmp_path
):
    announcement_app.announcement_service = support.build_service(
        announcement_app.ctrl,
        clock,
        doubles,
        tmp_path / "cache",
        discord=discord,
        public_origin=None,
    )
    assert client.get(f"{API}/status").get_json()["fallback"]["link_origin"] is None
    schedule = fail_speech(announcement_app, client, doubles, clock)
    (message,) = discord.messages
    assert "http" not in message
    assert message.endswith("Open Settings → Announcements in the home app.")
    assert item_of(client, schedule["id"])["fallback"]["link"] is None


def test_reminder_link_opens_the_authenticated_page_only(announcement_app, client, doubles):
    page = "/settings/announcements?occurrence=0b0c1f2e-0000-4000-8000-000000000000"
    assert client.get(page).status_code == 200
    anonymous = announcement_app.test_client()
    assert anonymous.get(page).status_code in (302, 401)
    other = support.login(announcement_app.test_client(), "admin")
    assert other.get(page).status_code == 302  # not Root-Admin: back to Settings


def test_fallback_state_has_a_place_on_the_reminder_page(announcement_app, client):
    html = client.get("/settings/announcements").get_data(as_text=True)
    assert 'id="announceStatusFallback"' in html
    assert 'id="announceStatusLink"' in html
    assert "Discord accepting it does not mean it was read" in html


def test_production_wiring_uses_the_alert_webhook_and_public_origin(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from app.core import Controller
    from app.core.database import DatabaseManager
    from app.core.schema import metadata
    from app.services.announcements import build_announcement_service

    monkeypatch.setattr(DatabaseManager, "_instance", None)
    monkeypatch.setenv("PIPER_COMMAND", str(tmp_path / "missing-piper"))
    monkeypatch.setenv("ALERT_WEBHOOK_URL", WEBHOOK_URL)
    monkeypatch.setenv("ANNOUNCEMENT_PUBLIC_ORIGIN", "https://jannenkoti.test/")
    ctrl = Controller(db_path=str(tmp_path / "legacy.db"))
    ctrl._sa_engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    metadata.create_all(ctrl._sa_engine)
    status = build_announcement_service(ctrl, db_path=str(tmp_path / "legacy.db")).status()
    assert status["fallback"]["available"] is True
    assert status["fallback"]["link_origin"] == "https://jannenkoti.test"
    assert WEBHOOK_URL not in str(status)  # the webhook token is never exposed
    monkeypatch.delenv("ALERT_WEBHOOK_URL")
    monkeypatch.setenv("ANNOUNCEMENT_PUBLIC_ORIGIN", "https://jannenkoti.test/settings")
    status = build_announcement_service(ctrl, db_path=str(tmp_path / "legacy.db")).status()
    assert status["fallback"]["reason"] == "not_configured"
    assert status["fallback"]["link_origin"] is None  # a path is not an origin
    ctrl._sa_engine.dispose()
    ctrl.db.conn.close()
