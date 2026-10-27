"""Recurring daily and weekday reminders through public operations.

Real Controller, isolated SQLite, real service policy; doubles only for the
Piper subprocess and the Cast transport. ``START`` is Friday 2027-01-15
12:00 UTC, i.e. 14:00 in Europe/Helsinki (UTC+2 in winter). Helsinki moves
to summer time on Sunday 2027-03-28 (03:00 -> 04:00) and back on Sunday
2027-10-31 (04:00 -> 03:00).
"""

from __future__ import annotations

from datetime import datetime

import announcement_support as support
from announcement_support import headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles

API = "/api/announcements/schedules"
EVERY_DAY = [1, 2, 3, 4, 5, 6, 7]


def save(app, client, expect=201, **body):
    payload = {
        "kind": "recurring",
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "recurrence": {"weekdays": EVERY_DAY, "times": ["08:00"]},
        **body,
    }
    response = client.post(API, json=payload, headers=headers(app, client))
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def at(clock, iso_utc):
    clock[0] = datetime.fromisoformat(iso_utc).timestamp()


def history(client, schedule_id=None):
    items = client.get("/api/announcements/occurrences?limit=200").get_json()["occurrences"]
    if schedule_id:
        items = [i for i in items if i["occurrence"]["schedule_id"] == schedule_id]
    return sorted(items, key=lambda i: i["occurrence"]["instance_due_at"])


def dues(items):
    return [i["occurrence"]["instance_due_at"] for i in items]


def finish_all(app, cast, clock):
    """Let every dispatched attempt play to the end."""
    service = app.announcement_service
    for play in cast.plays:
        host = "192.168.1.119" if play["target"] == "jbl-right" else "192.168.1.148"
        if cast.speakers[host].media.get(play["attempt_id"]) == "loading":
            cast.report(host, play["attempt_id"], "finished")
    clock[0] += 1
    service.tick()


def test_daily_reminder_with_several_times_shows_helsinki_upcoming_times(announcement_app, client):
    saved = save(
        announcement_app,
        client,
        request_id="recurring-request-1",
        recurrence={"weekdays": [7, 1, 2, 3, 4, 5, 6, 1], "times": ["20:30", "08:00"]},
    )
    schedule = saved["schedule"]
    assert schedule["kind"] == "recurring"
    assert schedule["recurrence"] == {"weekdays": EVERY_DAY, "times": ["08:00", "20:30"]}
    assert schedule["due_local"] is None
    assert schedule["due_at"] is None
    assert schedule["next_due_at"] == "2027-01-15T18:30:00+00:00"
    assert saved["upcoming"] == [
        {"due_at": "2027-01-15T18:30:00+00:00"},
        {"due_at": "2027-01-16T06:00:00+00:00"},
        {"due_at": "2027-01-16T18:30:00+00:00"},
        {"due_at": "2027-01-17T06:00:00+00:00"},
        {"due_at": "2027-01-17T18:30:00+00:00"},
    ]
    again = client.post(
        API,
        json={
            "kind": "recurring",
            "text": "Muista ottaa lääkkeet.",
            "target": "jbl-right",
            "recurrence": {"weekdays": EVERY_DAY, "times": ["08:00", "20:30"]},
            "request_id": "recurring-request-1",
        },
        headers=headers(announcement_app, client),
    )
    assert again.status_code == 200
    assert again.get_json()["schedule"]["id"] == schedule["id"]


def restart(app, clock, doubles, tmp_path):
    service = support.build_service(app.ctrl, clock, doubles, tmp_path / "cache")
    app.announcement_service = service
    return service


def test_each_instance_becomes_its_own_occurrence_once_across_ticks_and_restarts(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    saved = save(
        announcement_app,
        client,
        recurrence={"weekdays": EVERY_DAY, "times": ["08:00", "20:30"]},
        volume_percent=45,
    )["schedule"]
    service = announcement_app.announcement_service
    at(clock, "2027-01-15T18:30:00+00:00")  # Friday 20:30 Helsinki
    service.tick()
    service.tick()
    finish_all(announcement_app, cast, clock)
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    at(clock, "2027-01-16T06:00:00+00:00")  # Saturday 08:00 Helsinki
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    service.tick()
    items = history(client, saved["id"])
    assert dues(items) == ["2027-01-15T18:30:00+00:00", "2027-01-16T06:00:00+00:00"]
    assert len({i["occurrence"]["id"] for i in items}) == 2
    first, second = (i["occurrence"] for i in items)
    assert first["delivery"] == "delivered"
    assert first["volume_percent"] == 45  # snapshot of the configuration
    assert first["text"] == "Muista ottaa lääkkeet."
    assert first["expires_at"] == "2027-01-15T18:45:00+00:00"
    assert second["status"] == "announcing"
    assert len(cast.plays) == 2
    detail = client.get(f"{API}/{saved['id']}").get_json()
    assert detail["upcoming"][0] == {"due_at": "2027-01-16T18:30:00+00:00"}
    assert len(detail["occurrences"]) == 2


def test_selected_weekdays_only_and_local_time_follows_summer_time(announcement_app, client, clock):
    saved = save(
        announcement_app,
        client,
        recurrence={"weekdays": [1, 3, 5], "times": ["08:00"]},  # Mon, Wed, Fri
    )
    # Friday 14:00 now; next Monday 08:00 winter time is 06:00 UTC.
    assert [u["due_at"] for u in saved["upcoming"]] == [
        "2027-01-18T06:00:00+00:00",
        "2027-01-20T06:00:00+00:00",
        "2027-01-22T06:00:00+00:00",
        "2027-01-25T06:00:00+00:00",
        "2027-01-27T06:00:00+00:00",
    ]
    at(clock, "2027-03-24T12:00:00+00:00")  # Wednesday afternoon before the change
    detail = save(announcement_app, client, recurrence={"weekdays": [5, 1], "times": ["08:00"]})
    # Friday 26 March is still winter time; Monday 29 March is summer time.
    assert [u["due_at"] for u in detail["upcoming"][:2]] == [
        "2027-03-26T06:00:00+00:00",
        "2027-03-29T05:00:00+00:00",
    ]


def test_downtime_records_missed_instances_and_speaks_only_the_current_one(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    saved = save(
        announcement_app, client, recurrence={"weekdays": EVERY_DAY, "times": ["08:00", "20:30"]}
    )["schedule"]
    at(clock, "2027-01-17T06:05:00+00:00")  # down from Friday 14:00 to Sunday 08:05
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    service.tick()
    items = history(client, saved["id"])
    assert dues(items) == [
        "2027-01-15T18:30:00+00:00",
        "2027-01-16T06:00:00+00:00",
        "2027-01-16T18:30:00+00:00",
        "2027-01-17T06:00:00+00:00",
    ]
    for missed in items[:3]:
        occurrence = missed["occurrence"]
        assert occurrence["status"] == "closed"
        assert occurrence["disposition"] == "missed"
        assert occurrence["delivery"] == "none"
        assert occurrence["fallback_reason"] == "expired_undelivered"
        assert missed["attempts"] == []
    current = items[3]["occurrence"]
    assert current["status"] == "announcing"
    assert current["expires_at"] == "2027-01-17T06:15:00+00:00"
    assert len(cast.plays) == 1  # no burst of stale speech
    detail = client.get(f"{API}/{saved['id']}").get_json()
    assert detail["schedule"]["next_due_at"] == "2027-01-17T18:30:00+00:00"


def test_spring_gap_time_is_recorded_missed_and_other_times_continue(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    at(clock, "2027-03-27T12:00:00+00:00")  # Saturday before the spring change
    saved = save(
        announcement_app,
        client,
        recurrence={"weekdays": EVERY_DAY, "times": ["03:30", "08:00"]},
        quiet_hours_override=True,
    )
    assert saved["upcoming"][:3] == [
        {"due_at": "2027-03-28T01:30:00+00:00", "local": "2027-03-28T03:30", "missed": "dst_gap"},
        {"due_at": "2027-03-28T05:00:00+00:00"},
        {"due_at": "2027-03-29T00:30:00+00:00"},
    ]
    service = announcement_app.announcement_service
    for moment in ("2027-03-28T01:00:00+00:00", "2027-03-28T01:30:00+00:00"):
        at(clock, moment)
        service.tick()
    service.tick()
    assert cast.plays == []
    (gap,) = history(client, saved["schedule"]["id"])
    assert gap["occurrence"]["instance_local"] == "2027-03-28T03:30"
    assert gap["occurrence"]["status"] == "closed"
    assert gap["occurrence"]["disposition"] == "missed"
    assert gap["occurrence"]["disposition_reason"] == "dst_gap"
    assert gap["occurrence"]["delivery"] == "none"
    assert gap["occurrence"]["fallback_reason"] == "expired_undelivered"
    assert gap["attempts"] == []

    at(clock, "2027-03-28T05:00:00+00:00")  # 08:00 summer time
    service.tick()
    items = history(client, saved["schedule"]["id"])
    assert items[1]["occurrence"]["instance_local"] == "2027-03-28T08:00"
    assert items[1]["occurrence"]["status"] == "announcing"
    assert len(cast.plays) == 1


def test_autumn_repeated_time_runs_once_at_its_first_occurrence(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    at(clock, "2027-10-30T12:00:00+00:00")  # Saturday before the autumn change
    saved = save(
        announcement_app,
        client,
        recurrence={"weekdays": EVERY_DAY, "times": ["03:30"]},
        quiet_hours_override=True,
    )
    assert [u["due_at"] for u in saved["upcoming"][:2]] == [
        "2027-10-31T00:30:00+00:00",  # 03:30 summer time (first)
        "2027-11-01T01:30:00+00:00",  # 03:30 winter time next day
    ]
    service = announcement_app.announcement_service
    at(clock, "2027-10-31T00:30:00+00:00")
    service.tick()
    finish_all(announcement_app, cast, clock)
    at(clock, "2027-10-31T01:30:00+00:00")  # the clock shows 03:30 again
    service.tick()
    service.tick()
    items = history(client, saved["schedule"]["id"])
    assert dues(items) == ["2027-10-31T00:30:00+00:00"]
    assert items[0]["occurrence"]["delivery"] == "delivered"
    assert items[0]["occurrence"]["instance_local"] == "2027-10-31T03:30"
    assert len(cast.plays) == 1


def put(app, client, schedule_id, expect=200, **body):
    payload = {
        "kind": "recurring",
        "text": "Muista ottaa lääkkeet.",
        "target": "jbl-right",
        "recurrence": {"weekdays": EVERY_DAY, "times": ["08:00"]},
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


def test_edits_change_future_instances_and_keep_past_snapshots(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]
    at(clock, "2027-01-16T06:00:00+00:00")  # Saturday 08:00
    service.tick()
    finish_all(announcement_app, cast, clock)
    edited = put(
        announcement_app,
        client,
        saved["id"],
        revision=1,
        text="Mittaa verenpaine.",
        recurrence={"weekdays": [6, 7], "times": ["09:15"]},
    )
    assert edited["schedule"]["revision"] == 2
    # Today's 09:15 is still ahead, then Sunday, then next weekend.
    assert [u["due_at"] for u in edited["upcoming"][:3]] == [
        "2027-01-16T07:15:00+00:00",
        "2027-01-17T07:15:00+00:00",
        "2027-01-23T07:15:00+00:00",
    ]
    at(clock, "2027-01-16T07:15:00+00:00")
    service.tick()
    items = history(client, saved["id"])
    assert dues(items) == ["2027-01-16T06:00:00+00:00", "2027-01-16T07:15:00+00:00"]
    old, new = (i["occurrence"] for i in items)
    assert (old["text"], old["schedule_revision"]) == ("Muista ottaa lääkkeet.", 1)
    assert (new["text"], new["schedule_revision"]) == ("Mittaa verenpaine.", 2)
    assert old["delivery"] == "delivered"  # history is not rewritten by the edit

    switched = put(
        announcement_app,
        client,
        saved["id"],
        kind="one_time",
        due_local="2027-01-20T12:00",
        recurrence=None,
    )
    assert switched["schedule"]["recurrence"] is None
    assert switched["upcoming"] == [{"due_at": "2027-01-20T10:00:00+00:00"}]


def test_pause_and_delete_stop_pending_and_future_work_without_replay(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers["192.168.1.119"].state = "playing"  # music: the reminder waits
    saved = save(
        announcement_app, client, recurrence={"weekdays": EVERY_DAY, "times": ["08:00", "12:00"]}
    )["schedule"]
    at(clock, "2027-01-16T06:00:00+00:00")
    service.tick()
    paused = act(announcement_app, client, saved["id"], "pause")
    assert paused["upcoming"] == []
    (pending,) = history(client, saved["id"])
    assert pending["occurrence"]["disposition"] == "cancelled"
    assert pending["occurrence"]["fallback_reason"] is None

    cast.speakers["192.168.1.119"].state = "idle"
    at(clock, "2027-01-16T10:05:00+00:00")  # 12:05: the 12:00 instance passed while paused
    service.tick()
    resumed = act(announcement_app, client, saved["id"], "resume")
    assert resumed["upcoming"][0] == {"due_at": "2027-01-17T06:00:00+00:00"}
    service.tick()
    assert cast.plays == []  # neither the cancelled 08:00 nor the paused 12:00 replay
    assert dues(history(client, saved["id"])) == ["2027-01-16T06:00:00+00:00"]

    at(clock, "2027-01-17T06:00:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    deleted = act(announcement_app, client, saved["id"], "delete")
    assert deleted["schedule"]["status"] == "deleted"
    assert deleted["upcoming"] == []
    at(clock, "2027-01-18T06:00:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    kept = history(client, saved["id"])
    assert dues(kept) == ["2027-01-16T06:00:00+00:00", "2027-01-17T06:00:00+00:00"]


def test_recurring_reminders_keep_quiet_hours_expiry_and_music_rules(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    at(clock, "2027-01-15T20:30:00+00:00")  # Friday 22:30
    saved = save(
        announcement_app,
        client,
        recurrence={"weekdays": EVERY_DAY, "times": ["06:50", "21:55"]},
        expiry_minutes=20,
    )["schedule"]
    at(clock, "2027-01-16T04:50:00+00:00")  # 06:50: quiet hours until 07:00
    service.tick()
    (held,) = history(client, saved["id"])
    assert held["occurrence"]["waiting_reason"] == "quiet_hours"
    at(clock, "2027-01-16T05:00:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    finish_all(announcement_app, cast, clock)

    cast.speakers["192.168.1.119"].state = "playing"
    at(clock, "2027-01-16T19:55:00+00:00")  # 21:55, music playing
    service.tick()
    at(clock, "2027-01-16T20:00:00+00:00")  # 22:00 quiet hours begin
    cast.speakers["192.168.1.119"].state = "idle"
    service.tick()
    at(clock, "2027-01-16T20:15:00+00:00")  # expiry passes while held
    service.tick()
    evening = history(client, saved["id"])[1]["occurrence"]
    assert evening["disposition"] == "missed"
    assert evening["disposition_reason"] == "quiet_hours"
    assert [h["reason"] for h in evening["waiting_history"]] == ["music_playing", "quiet_hours"]
    assert len(cast.plays) == 1


def test_invalid_recurrence_is_rejected_with_the_field_to_fix(announcement_app, client):
    cases = [
        ({"recurrence": None}, "recurrence"),
        ({"recurrence": {"weekdays": [], "times": ["08:00"]}}, "weekdays"),
        ({"recurrence": {"weekdays": [0, 8], "times": ["08:00"]}}, "weekdays"),
        ({"recurrence": {"weekdays": [1], "times": []}}, "times"),
        ({"recurrence": {"weekdays": [1], "times": ["24:00"]}}, "times"),
        ({"recurrence": {"weekdays": [1], "times": ["8:00"]}}, "times"),
        ({"recurrence": {"weekdays": [1], "times": [f"{h:02}:00" for h in range(13)]}}, "times"),
        ({"kind": "hourly"}, "kind"),
    ]
    for body, field in cases:
        data = save(announcement_app, client, expect=400, **body)
        assert data["field"] == field, (body, data)
    assert client.get(API).get_json()["schedules"] == []


def test_recurring_controls_are_on_the_announcement_page(announcement_app, client):
    html = client.get("/settings/announcements").get_data(as_text=True)
    for element in (
        'name="announceRepeat" value="once"',
        'name="announceRepeat" value="daily"',
        'name="announceRepeat" value="weekdays"',
        'id="announceWeekdayGroup"',
        'id="announceTimesList"',
        'id="announceAddTime"',
        'id="announceRecurrenceError"',
    ):
        assert element in html
    assert "Europe/Helsinki local time" in html
    assert "js/announcements.js?v=20261027-4" in html
    assert "css/announcements.css?v=20261027-4" in html
