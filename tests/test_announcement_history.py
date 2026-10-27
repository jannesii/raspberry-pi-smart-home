"""Deliberate history deletion through authenticated public operations."""

import announcement_support as support
from announcement_support import headers
from test_announcement_acknowledgements import API, at, only_occurrence, play_through, save

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord


def erase(app, client, kind, identity, *, confirm=True, expect=200):
    response = client.delete(
        f"{API}/{kind}/{identity}", json={"confirm": confirm}, headers=headers(app, client)
    )
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def test_delete_terminal_occurrence_removes_history_but_keeps_schedule(
    announcement_app, client, doubles, clock
):
    schedule = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    item = only_occurrence(client, schedule["id"])
    identity = item["occurrence"]["id"]
    erase(announcement_app, client, "occurrences", identity, expect=409)
    play_through(announcement_app.announcement_service, doubles[1], clock)
    erase(announcement_app, client, "occurrences", identity, confirm=False, expect=400)
    erase(announcement_app, client, "occurrences", identity)
    erase(announcement_app, client, "occurrences", identity)
    assert client.get(f"{API}/occurrences/{identity}").status_code == 404
    detail = client.get(f"{API}/schedules/{schedule['id']}").get_json()
    assert detail["occurrences"] == []
    assert detail["schedule"]["status"] == "active"
    announcement_app.announcement_service.tick()
    assert len(doubles[1].plays) == 1


def test_unopened_and_retrying_fallbacks_are_not_deleted(
    announcement_app, client, doubles, discord, clock
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[0].failure = "voice_unavailable"
    discord.outcomes = [support.DiscordResponse(503), support.DiscordResponse(204)]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    item = only_occurrence(client, schedule["id"])
    identity = item["occurrence"]["id"]
    assert item["fallback"]["status"] == "pending"
    erase(announcement_app, client, "occurrences", identity, expect=409)
    assert only_occurrence(client, schedule["id"])["fallback"] == item["fallback"]
    clock[0] += 301
    service.tick()
    assert only_occurrence(client, schedule["id"])["fallback"]["status"] == "accepted"
    erase(announcement_app, client, "occurrences", identity)
    for _ in range(3):
        service.tick()
    assert len(discord.requests) == 2


def test_speaker_test_deletion_preserves_request_deduplication(
    announcement_app, client, doubles, clock
):
    payload = {"text": "Private test", "target": "jbl-right", "request_id": "history-test-request"}
    response = client.post(f"{API}/tests", json=payload, headers=headers(announcement_app, client))
    identity = response.get_json()["test"]["id"]
    erase(announcement_app, client, "tests", identity, expect=409)
    service = announcement_app.announcement_service
    service.tick()
    play_through(service, doubles[1], clock)
    erase(announcement_app, client, "tests", identity)
    assert client.get(f"{API}/tests/{identity}").status_code == 404
    response = client.post(f"{API}/tests", json=payload, headers=headers(announcement_app, client))
    assert response.status_code == 400
    service.tick()
    assert len(doubles[1].plays) == 1


def test_history_deletion_requires_root_admin_and_csrf(announcement_app, client):
    path = f"{API}/occurrences/missing"
    assert client.delete(path, json={"confirm": True}).status_code == 400
    admin = support.login(announcement_app.test_client(), "admin")
    assert (
        admin.delete(
            path, json={"confirm": True}, headers=headers(announcement_app, admin)
        ).status_code
        == 403
    )
    anonymous = announcement_app.test_client()
    assert anonymous.delete(
        path, json={"confirm": True}, headers=headers(announcement_app, anonymous)
    ).status_code in (302, 401)


def test_deleting_acknowledged_snooze_erases_actions_and_keeps_future_work(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].speakers["192.168.1.119"].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    identity = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    response = client.post(
        f"{API}/occurrences/{identity}/snooze",
        json={"request_id": "delete-snooze-history", "minutes": 10},
        headers=headers(announcement_app, client),
    )
    assert response.status_code == 200
    response = client.post(
        f"{API}/occurrences/{identity}/done", json={}, headers=headers(announcement_app, client)
    )
    assert response.status_code == 200
    assert response.json["snoozes"][0]["minutes"] == 10
    assert response.json["acknowledgement"]["action"] == "done"
    erase(announcement_app, client, "occurrences", identity)
    assert client.get(f"{API}/occurrences/{identity}").status_code == 404
    assert (
        client.post(
            f"{API}/occurrences/{identity}/done", json={}, headers=headers(announcement_app, client)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/occurrences/{identity}/snooze",
            json={"request_id": "delete-snooze-history"},
            headers=headers(announcement_app, client),
        ).status_code
        == 404
    )
    clock[0] += 600
    service.tick()
    assert doubles[1].plays == []


def test_deletion_during_volume_restoration_is_refused(announcement_app, client, doubles, clock):
    service = announcement_app.announcement_service
    response = client.post(
        f"{API}/tests",
        json={"text": "Volume restoration", "target": "jbl-right"},
        headers=headers(announcement_app, client),
    )
    identity = response.json["test"]["id"]
    service.tick()
    cast = doubles[1]
    original = cast.set_volume
    checked = []

    def during_restore(target, volume):
        if volume == 0.4:
            response = client.delete(
                f"{API}/tests/{identity}",
                json={"confirm": True},
                headers=headers(announcement_app, client),
            )
            checked.append(response.status_code)
        return original(target, volume)

    cast.set_volume = during_restore
    play_through(service, cast, clock)
    assert checked == [409]
    assert client.get(f"{API}/tests/{identity}").json["attempt"]["volume_restore"] == "restored"
    erase(announcement_app, client, "tests", identity)


def test_history_deletion_cannot_reset_waiting_automatic_retry_budget(
    announcement_app, client, doubles, clock
):
    from app.services.announcements import TransportError

    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].play_error = TransportError("connection_refused", certain_not_played=True)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    identity = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    before = only_occurrence(client, schedule["id"])
    assert before["occurrence"]["retries_started"] == 1
    erase(announcement_app, client, "occurrences", identity, expect=409)
    assert only_occurrence(client, schedule["id"]) == before
    at(clock, "2027-01-15T12:32:00+00:00")
    service.tick()
    after = only_occurrence(client, schedule["id"])
    assert after["occurrence"]["retries_started"] == 2
    assert len(after["attempts"]) == 3
    erase(announcement_app, client, "occurrences", identity)
    service.tick()
    assert client.get(f"{API}/occurrences/{identity}").status_code == 404
