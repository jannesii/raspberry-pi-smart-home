"""Deliberate snooze and supersession through authenticated public operations."""

import announcement_support as support
import pytest
from announcement_support import headers
from test_announcement_acknowledgements import API, RIGHT, at, only_occurrence, play_through, save

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord


def snooze(app, client, occurrence_id, *, request_id="snooze-request-0001", expect=200, **body):
    response = client.post(
        f"{API}/occurrences/{occurrence_id}/snooze",
        json={"request_id": request_id, **body},
        headers=headers(app, client),
    )
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def test_snooze_moves_same_occurrence_beyond_original_expiry_and_survives_restart(
    announcement_app, client, doubles, clock, tmp_path
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client, expiry_minutes=2)["schedule"]
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    original = only_occurrence(client, schedule["id"])["occurrence"]
    item = snooze(announcement_app, client, original["id"])
    occurrence = item["occurrence"]
    assert occurrence["id"] == original["id"]
    assert occurrence["instance_due_at"] == "2027-01-15T12:30:00+00:00"
    assert occurrence["due_at"] == "2027-01-15T12:40:00+00:00"
    assert occurrence["expires_at"] == "2027-01-15T12:42:00+00:00"
    assert item["acknowledgement"] is None
    assert item["snoozes"][0]["minutes"] == 10
    assert occurrence["announcements_started"] == 0
    at(clock, "2027-01-15T12:35:00+00:00")
    service = support.build_service(announcement_app.ctrl, clock, doubles, tmp_path / "restart")
    announcement_app.announcement_service = service
    again = snooze(announcement_app, client, original["id"])
    assert again["snoozes"] == item["snoozes"]
    assert again["occurrence"]["due_at"] == occurrence["due_at"]
    doubles[1].speakers[RIGHT].state = "idle"
    service.tick()
    assert doubles[1].plays == []
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert len(doubles[1].plays) == 1
    assert (
        only_occurrence(client, schedule["id"])["attempts"][0]["dispatch_deadline"]
        == occurrence["expires_at"]
    )


def test_new_recurrence_supersedes_snoozed_work_without_acknowledging(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(
        announcement_app,
        client,
        kind="recurring",
        recurrence={"weekdays": [5], "times": ["14:30", "14:35"]},
    )["schedule"]
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    old = only_occurrence(client, schedule["id"])
    snooze(announcement_app, client, old["occurrence"]["id"], minutes=30)
    at(clock, "2027-01-15T12:35:00+00:00")
    doubles[1].speakers[RIGHT].state = "idle"
    service.tick()
    item = client.get(f"{API}/occurrences/{old['occurrence']['id']}").get_json()
    assert item["occurrence"]["status"] == "closed"
    assert item["occurrence"]["disposition"] == "superseded"
    assert item["acknowledgement"] is None
    assert item["snoozes"][0]["minutes"] == 30
    assert item["attempts"] == []
    assert len(doubles[1].plays) == 1
    snooze(
        announcement_app,
        client,
        old["occurrence"]["id"],
        request_id="new-snooze-request",
        expect=409,
    )
    play_through(service, doubles[1], clock)
    at(clock, "2027-01-15T13:00:00+00:00")
    service.tick()
    assert len(doubles[1].plays) == 1


def test_snooze_during_synthesis_prevents_stale_dispatch(announcement_app, client, doubles, clock):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]

    def postpone():
        item = only_occurrence(client, schedule["id"])
        snooze(announcement_app, client, item["occurrence"]["id"])

    doubles[0].during = postpone
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    assert doubles[1].plays == []
    item = only_occurrence(client, schedule["id"])
    assert item["attempts"] == []
    assert item["occurrence"]["next_attempt_at"] == "2027-01-15T12:40:00+00:00"


def test_stop_then_snooze_stops_old_attempt_and_preserves_budgets(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    old = only_occurrence(client, schedule["id"])
    oid = old["occurrence"]["id"]
    response = client.post(
        f"{API}/occurrences/{oid}/stop", headers=headers(announcement_app, client)
    )
    assert response.status_code == 202
    stopped = response.get_json()
    assert "snooze" in stopped["actions"]
    item = snooze(announcement_app, client, oid)
    assert item["occurrence"]["announcements_started"] == 1
    service.tick()  # old stop completion must not close the new snooze
    item = client.get(f"{API}/occurrences/{oid}").get_json()
    assert item["occurrence"]["status"] == "waiting"
    assert item["occurrence"]["next_attempt_at"] == "2027-01-15T12:40:00+00:00"
    assert item["attempts"][0]["status"] == "stopped"
    assert item["acknowledgement"] is None
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    item = client.get(f"{API}/occurrences/{oid}").get_json()
    assert item["occurrence"]["announcements_started"] == 2
    assert item["occurrence"]["max_announcements"] == 1
    assert item["occurrence"]["status"] == "closed"
    assert len(item["snoozes"]) == 1
    assert len(doubles[1].plays) == 2


def test_snooze_controls_explain_choices_and_deadline(announcement_app, client):
    html = client.get("/settings/announcements").get_data(as_text=True)
    assert "js/announcements.js?v=20261101-1" in html
    script = client.get("/static/js/announcements.js").get_data(as_text=True)
    assert "[5, 10, 15, 30]" in script
    assert "Snooze minutes:" in script
    assert "Quiet hours still apply" in script


def test_stop_then_snooze_does_not_reenable_cancelled_repeats(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client, max_announcements=5, repeat_interval_minutes=1)[
        "schedule"
    ]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    client.post(f"{API}/occurrences/{oid}/stop", headers=headers(announcement_app, client))
    snooze(announcement_app, client, oid)
    service.tick()
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    play_through(service, doubles[1], clock)
    item = client.get(f"{API}/occurrences/{oid}").get_json()
    assert item["occurrence"]["status"] == "closed"
    at(clock, "2027-01-15T12:44:00+00:00")
    service.tick()
    assert len(doubles[1].plays) == 2


def test_stopped_old_occurrence_cannot_be_revived_after_a_new_instance(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(
        announcement_app,
        client,
        kind="recurring",
        recurrence={"weekdays": [5], "times": ["14:30", "14:35"]},
    )["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    client.post(f"{API}/occurrences/{oid}/stop", headers=headers(announcement_app, client))
    service.tick()
    at(clock, "2027-01-15T12:35:00+00:00")
    service.tick()
    snooze(announcement_app, client, oid, expect=409)
    assert "snooze" not in client.get(f"{API}/occurrences/{oid}").get_json()["actions"]


@pytest.mark.parametrize("minutes", [5, 10, 15, 30])
def test_snooze_choices_and_duplicate_payload_conflicts(
    announcement_app, client, doubles, clock, minutes
):
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    item = snooze(announcement_app, client, oid, minutes=minutes)
    assert item["snoozes"][0]["minutes"] == minutes
    assert item["occurrence"]["urgent"] is False
    assert item["occurrence"]["quiet_hours"] == "22:00-07:00"
    snooze(announcement_app, client, oid, minutes=5 if minutes != 5 else 10, expect=409)
    for invalid in (0, 1, 11, 31, True, 5.0, "10", None):
        response = snooze(
            announcement_app,
            client,
            oid,
            request_id="invalid-snooze-request",
            minutes=invalid,
            expect=400,
        )
        assert response["field"] == "minutes"


@pytest.mark.parametrize("action", ["done", "skip", "pause", "delete"])
def test_actions_cancel_snooze_at_dispatch_boundary(
    announcement_app, client, doubles, clock, action, tmp_path
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    snooze(announcement_app, client, oid)

    def cancel():
        if action in ("done", "skip"):
            url = f"{API}/occurrences/{oid}/{action}"
            result = client.post(url, headers=headers(announcement_app, client))
        elif action == "pause":
            result = client.post(
                f"{API}/schedules/{schedule['id']}/pause", headers=headers(announcement_app, client)
            )
        else:
            result = client.delete(
                f"{API}/schedules/{schedule['id']}", headers=headers(announcement_app, client)
            )
        assert result.status_code == 200

    service = support.build_service(announcement_app.ctrl, clock, doubles, tmp_path / "race-cache")
    announcement_app.announcement_service = service
    doubles[0].during = cancel
    doubles[1].speakers[RIGHT].state = "idle"
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert doubles[1].plays == []
    item = client.get(f"{API}/occurrences/{oid}").get_json()
    assert item["occurrence"]["status"] == "closed"
    assert item["attempts"] == []
    snooze(announcement_app, client, oid, request_id="later-snooze-request", expect=409)


@pytest.mark.parametrize(
    "override,urgent,plays", [(False, False, 0), (False, True, 0), (True, False, 1)]
)
def test_snoozed_delivery_respects_quiet_hours_independently_of_urgency(
    announcement_app, client, doubles, clock, override, urgent, plays
):
    service = announcement_app.announcement_service
    schedule = save(
        announcement_app,
        client,
        due_local="2027-01-15T21:59",
        expiry_minutes=2,
        quiet_hours_override=override,
        urgent=urgent,
    )["schedule"]
    at(clock, "2027-01-15T19:59:00+00:00")
    service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    snooze(announcement_app, client, oid)
    service.tick()  # terminate previous speech without acknowledging
    before = len(doubles[1].plays)
    at(clock, "2027-01-15T20:09:00+00:00")
    service.tick()
    assert len(doubles[1].plays) - before == plays
    item = client.get(f"{API}/occurrences/{oid}").get_json()
    assert item["occurrence"]["expires_at"] == "2027-01-15T20:11:00+00:00"
    if not override:
        assert item["occurrence"]["waiting_reason"] == "quiet_hours"
        at(clock, "2027-01-15T20:11:00+00:00")
        service.tick()
        assert (
            client.get(f"{API}/occurrences/{oid}").get_json()["occurrence"]["disposition"]
            == "missed"
        )


def test_snooze_preserves_exhausted_retry_budget_and_adjusted_expiry(
    announcement_app, client, doubles, clock, tmp_path
):
    from app.services.announcements import TransportError

    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].play_error = TransportError("connection_refused", certain_not_played=True)
    at(clock, "2027-01-15T12:30:00+00:00")
    for _ in range(3):
        service.tick()
        clock[0] += 60
    item = only_occurrence(client, schedule["id"])
    assert item["occurrence"]["retries_started"] == 2
    snoozed = snooze(announcement_app, client, item["occurrence"]["id"])
    assert snoozed["occurrence"]["expires_at"] == "2027-01-15T12:58:00+00:00"
    service = support.build_service(announcement_app.ctrl, clock, doubles, tmp_path / "restart")
    announcement_app.announcement_service = service
    at(clock, "2027-01-15T12:43:00+00:00")
    service.tick()
    again = only_occurrence(client, schedule["id"])
    assert again["occurrence"]["retries_started"] == 2
    assert again["occurrence"]["announcements_started"] == 2
    assert again["occurrence"]["status"] == "closed"
    assert again["occurrence"]["expires_at"] == snoozed["occurrence"]["expires_at"]
    assert len(again["attempts"]) == 4


def test_snooze_requires_root_and_csrf(announcement_app, client, doubles, clock):
    save(announcement_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    announcement_app.announcement_service.tick()
    (item,) = client.get(f"{API}/occurrences").get_json()["occurrences"]
    url = f"{API}/occurrences/{item['occurrence']['id']}/snooze"
    anonymous = announcement_app.test_client()
    admin = support.login(announcement_app.test_client(), "admin")
    assert anonymous.post(url).status_code in (400, 401)
    assert admin.post(url, headers=headers(announcement_app, admin)).status_code == 403
    assert client.post(url, json={"request_id": "csrf-snooze-request"}).status_code == 400
    assert client.get(f"{API}/occurrences/{item['occurrence']['id']}").get_json()["snoozes"] == []


def test_clock_crossing_next_instance_during_preflight_cannot_dispatch_old_work(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(
        announcement_app,
        client,
        kind="recurring",
        recurrence={"weekdays": [5], "times": ["14:30", "14:35"]},
    )["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    doubles[0].during = lambda: at(clock, "2027-01-15T12:35:00+00:00")
    service.tick()
    assert doubles[1].plays == []
    old = only_occurrence(client, schedule["id"])
    assert old["attempts"] == []
    service.tick()
    assert len(doubles[1].plays) == 1
    old = client.get(f"{API}/occurrences/{old['occurrence']['id']}").get_json()
    assert old["occurrence"]["disposition"] == "superseded"
    assert old["acknowledgement"] is None


def test_repeated_deliberate_snoozes_keep_the_original_window(
    announcement_app, client, doubles, clock
):
    service = announcement_app.announcement_service
    schedule = save(announcement_app, client)["schedule"]
    doubles[1].speakers[RIGHT].state = "playing"
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    oid = only_occurrence(client, schedule["id"])["occurrence"]["id"]
    snooze(announcement_app, client, oid, minutes=30, request_id="z-first-request")
    snooze(announcement_app, client, oid, minutes=5, request_id="y-second-request")
    snooze(announcement_app, client, oid, minutes=10, request_id="a-third-request")
    # Reordered histories sharing a timestamp must not change the 15-min window.
    at(clock, "2027-01-15T13:00:00+00:00")
    item = snooze(announcement_app, client, oid, minutes=30, request_id="last-request")
    assert item["occurrence"]["due_at"] == "2027-01-15T13:30:00+00:00"
    assert item["occurrence"]["expires_at"] == "2027-01-15T13:45:00+00:00"
    assert item["occurrence"]["announcements_started"] == 0
