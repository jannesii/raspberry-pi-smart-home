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


def act(app, client, occurrence_id, action, expect=200):
    response = client.post(
        f"{API}/occurrences/{occurrence_id}/{action}", headers=headers(app, client)
    )
    assert response.status_code == expect, response.get_json()
    return response.get_json()


def test_done_acknowledges_a_delivered_reminder_and_stops_its_remaining_repeats(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=3, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    pending = only_occurrence(client, sid)
    assert pending["acknowledgement"] is None  # playback is not acknowledgement
    assert pending["actions"] == ["done", "skip", "stop"]

    at(clock, "2027-01-15T12:32:00+00:00")
    done = act(announcement_app, client, pending["occurrence"]["id"], "done")
    assert done["occurrence"]["status"] == "closed"
    assert done["occurrence"]["disposition"] == "done"
    assert done["occurrence"]["delivery"] == "delivered"  # evidence is kept as it was
    assert done["occurrence"]["fallback_reason"] is None
    ack = done["acknowledgement"]
    assert ack["action"] == "done"
    assert ack["acknowledged_at"] == "2027-01-15T12:32:00+00:00"
    assert ack["acknowledged_by"] == "owner"
    assert (ack["prior_status"], ack["prior_delivery"]) == ("waiting", "delivered")
    assert ack["announcements_started"] == 1
    assert done["actions"] == []

    again = act(announcement_app, client, pending["occurrence"]["id"], "done")
    assert again["acknowledgement"] == ack  # a repeated request changes nothing
    conflict = act(announcement_app, client, pending["occurrence"]["id"], "skip", expect=409)
    assert conflict["error"] == "already_acknowledged"
    act(announcement_app, client, "missing-occurrence", "done", expect=404)
    act(announcement_app, client, pending["occurrence"]["id"], "snore", expect=404)

    for minute in ("12:34", "12:38", "12:44"):
        at(clock, f"2027-01-15T{minute}:00+00:00")
        service.tick()
    assert len(cast.plays) == 1
    final = only_occurrence(client, sid)["occurrence"]
    assert final["disposition"] == "done"
    assert final["announcements_started"] == 1


def test_skip_suppresses_one_waiting_instance_and_the_schedule_continues(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers[RIGHT].state = "playing"
    saved = save(
        announcement_app,
        client,
        kind="recurring",
        due_local=None,
        recurrence={"weekdays": [1, 2, 3, 4, 5, 6, 7], "times": ["14:30"]},
    )["schedule"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    held = only_occurrence(client, saved["id"])
    assert held["occurrence"]["waiting_reason"] == "music_playing"
    assert held["actions"] == ["done", "skip"]  # nothing is speaking, so no Stop

    skipped = act(announcement_app, client, held["occurrence"]["id"], "skip")
    assert skipped["occurrence"]["status"] == "closed"
    assert skipped["occurrence"]["disposition"] == "skipped"
    assert skipped["occurrence"]["delivery"] == "none"
    assert skipped["acknowledgement"]["action"] == "skipped"
    assert skipped["acknowledgement"]["prior_status"] == "waiting"

    cast.speakers[RIGHT].state = "idle"
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    at(clock, "2027-01-15T12:46:00+00:00")
    service.tick()
    assert cast.plays == []
    occurrence = client.get(f"{API}/occurrences/{held['occurrence']['id']}").get_json()
    assert occurrence["occurrence"]["disposition"] == "skipped"
    assert occurrence["occurrence"]["fallback_reason"] is None  # skipping is not a failure

    schedule = client.get(f"{API}/schedules/{saved['id']}").get_json()
    assert schedule["schedule"]["status"] == "active"
    assert schedule["upcoming"][0]["due_at"] == "2027-01-16T12:30:00+00:00"
    at(clock, "2027-01-16T12:30:00+00:00")
    service.tick()
    assert len(cast.plays) == 1


def test_done_while_speaking_lets_the_message_finish_and_prevents_repeats_and_fallback(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=5)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    speaking = only_occurrence(client, saved["schedule"]["id"])
    assert speaking["occurrence"]["status"] == "announcing"
    done = act(announcement_app, client, speaking["occurrence"]["id"], "done")
    assert done["occurrence"]["status"] == "announcing"
    assert done["acknowledgement"]["prior_status"] == "announcing"
    assert done["actions"] == ["stop"]  # current speech can still be stopped
    assert ("stop", "jbl-right", cast.plays[0]["attempt_id"]) not in cast.effects

    cast.report(RIGHT, cast.plays[0]["attempt_id"], "failed")
    clock[0] += 2
    service.tick()
    occurrence = only_occurrence(client, saved["schedule"]["id"])["occurrence"]
    assert occurrence["status"] == "closed"
    assert occurrence["delivery"] == "failed"
    assert occurrence["disposition"] == "done"
    assert occurrence["fallback_reason"] is None  # the owner already handled it
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert len(cast.plays) == 1


def test_done_during_dispatch_preparation_wins_and_nothing_plays(
    announcement_app, client, doubles, clock
):
    synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client)["schedule"]

    def acknowledge():
        occurrence = only_occurrence(client, saved["id"])["occurrence"]
        act(announcement_app, client, occurrence["id"], "done")

    synth.during = acknowledge
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    service.tick()
    item = only_occurrence(client, saved["id"])
    assert item["occurrence"]["disposition"] == "done"
    assert item["attempts"] == []
    assert cast.effects == []


def test_stop_speaking_ends_speech_and_repeats_without_acknowledging(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=3, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    attempt_id = cast.plays[0]["attempt_id"]
    cast.report(RIGHT, attempt_id, "playing")
    clock[0] += 1
    service.tick()
    occurrence_id = only_occurrence(client, sid)["occurrence"]["id"]

    requested = act(announcement_app, client, occurrence_id, "stop", expect=202)
    assert requested["attempts"][0]["stop_requested_at"] == "2027-01-15T12:30:01+00:00"
    assert requested["attempts"][0]["stop_outcome"] is None  # sent is not stopped
    assert requested["acknowledgement"] is None
    assert "stop" not in requested["actions"]
    clock[0] += 1
    service.tick()
    stopped = only_occurrence(client, sid)
    assert ("stop", "jbl-right", attempt_id) in cast.effects
    assert stopped["attempts"][0]["status"] == "stopped"
    assert stopped["attempts"][0]["stop_outcome"] == "stopped"
    assert stopped["attempts"][0]["stop_observed_at"] == "2027-01-15T12:30:02+00:00"
    occurrence = stopped["occurrence"]
    assert occurrence["status"] == "closed"
    assert occurrence["delivery"] == "delivered"  # playback was observed before the stop
    assert occurrence["disposition"] == "stopped"
    assert occurrence["fallback_reason"] is None
    assert stopped["acknowledgement"] is None
    assert stopped["actions"] == ["done", "skip"]

    at(clock, "2027-01-15T12:44:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    act(announcement_app, client, occurrence_id, "stop", expect=409)
    done = act(announcement_app, client, occurrence_id, "done")
    assert done["occurrence"]["disposition"] == "done"
    assert done["acknowledgement"]["prior_disposition"] == "stopped"


def test_unconfirmed_stop_is_recorded_as_reported_and_still_cancels_repeats(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.stop_result = "failed"
    saved = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    attempt_id = cast.plays[0]["attempt_id"]
    cast.report(RIGHT, attempt_id, "playing")
    clock[0] += 1
    service.tick()
    act(announcement_app, client, only_occurrence(client, sid)["occurrence"]["id"], "stop", 202)
    clock[0] += 1
    service.tick()
    item = only_occurrence(client, sid)
    assert item["attempts"][0]["status"] == "playing"
    assert item["attempts"][0]["stop_outcome"] == "failed"
    cast.report(RIGHT, attempt_id, "finished")
    clock[0] += 3
    service.tick()
    item = only_occurrence(client, sid)
    assert item["attempts"][0]["status"] == "finished"
    assert item["attempts"][0]["stop_outcome"] == "failed"
    assert item["occurrence"]["status"] == "closed"
    assert item["occurrence"]["disposition"] == "stopped"
    assert item["occurrence"]["fallback_reason"] is None
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert len(cast.plays) == 1


def test_stop_between_repeats_cancels_them_and_needs_started_speech(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.speakers[RIGHT].state = "playing"
    saved = save(announcement_app, client, max_announcements=3, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    held = only_occurrence(client, sid)["occurrence"]
    refused = act(announcement_app, client, held["id"], "stop", expect=409)
    assert refused["error"] == "not_speaking"

    cast.speakers[RIGHT].state = "idle"
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    assert only_occurrence(client, sid)["occurrence"]["next_attempt_purpose"] == "repeat"
    stopped = act(announcement_app, client, held["id"], "stop", expect=202)
    assert stopped["occurrence"]["status"] == "closed"
    assert stopped["occurrence"]["disposition"] == "stopped"
    assert stopped["occurrence"]["delivery"] == "delivered"
    assert stopped["occurrence"]["fallback_reason"] is None
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert len(cast.plays) == 1
    act(announcement_app, client, "missing-occurrence", "stop", expect=404)


def restart(app, clock, doubles, tmp_path):
    service = support.build_service(app.ctrl, clock, doubles, tmp_path / "cache")
    app.announcement_service = service
    return service


def test_restart_keeps_counts_acknowledgements_and_stops(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    counted = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=4)
    acked = save(
        announcement_app,
        client,
        due_local="2027-01-15T14:31",
        max_announcements=2,
        repeat_interval_minutes=4,
        target="jbl-left",
    )
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    at(clock, "2027-01-15T12:31:00+00:00")
    service.tick()
    left = "192.168.1.148"
    cast.report(left, cast.plays[-1]["attempt_id"], "finished")
    clock[0] += 2
    service.tick()
    acked_id = only_occurrence(client, acked["schedule"]["id"])["occurrence"]["id"]
    act(announcement_app, client, acked_id, "skip")

    at(clock, "2027-01-15T12:34:00+00:00")
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    assert len(cast.plays) == 3  # the counted reminder's single repeat
    play_through(service, cast, clock)
    service = restart(announcement_app, clock, doubles, tmp_path)
    at(clock, "2027-01-15T12:44:00+00:00")
    service.tick()
    assert len(cast.plays) == 3
    first = only_occurrence(client, counted["schedule"]["id"])["occurrence"]
    assert first["announcements_started"] == 2
    assert first["status"] == "closed"
    second = only_occurrence(client, acked["schedule"]["id"])
    assert second["occurrence"]["disposition"] == "skipped"
    assert second["acknowledgement"]["action"] == "skipped"


def test_stop_requested_before_a_crash_is_not_replayed_or_escalated(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    saved = save(announcement_app, client, max_announcements=2, repeat_interval_minutes=4)
    sid = saved["schedule"]["id"]
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    act(announcement_app, client, only_occurrence(client, sid)["occurrence"]["id"], "stop", 202)
    clock[0] += 5  # the process dies before the stop reaches the speaker
    service = restart(announcement_app, clock, doubles, tmp_path)
    service.tick()
    item = only_occurrence(client, sid)
    assert item["attempts"][0]["status"] == "unknown"
    assert item["attempts"][0]["stop_outcome"] == "unknown"
    assert item["occurrence"]["status"] == "closed"
    assert item["occurrence"]["disposition"] == "stopped"
    assert item["occurrence"]["fallback_reason"] is None
    at(clock, "2027-01-15T12:40:00+00:00")
    service.tick()
    assert len(cast.plays) == 1


def _health_and_other_records(ctrl):
    """Every row outside the announcement tables, plus the refill calculations."""
    from sqlalchemy import inspect, select

    from app.core.schema import metadata

    engine = ctrl._sa_engine
    names = [n for n in inspect(engine).get_table_names() if not n.startswith("announcement_")]
    with engine.connect() as conn:
        rows = {
            name: sorted(map(repr, conn.execute(select(metadata.tables[name])).all()))
            for name in names
            if name in metadata.tables
        }
    purchases = ctrl.list_medicine_purchases()
    refills = [ctrl.calculate_medicine_purchase(p) for p in purchases]
    return rows, purchases, refills, ctrl.get_medicine_summaries()


def test_acknowledgements_never_create_health_records_or_change_refill_data(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    ctrl = announcement_app.ctrl
    ctrl.create_medicine_purchase(
        medicine_name="Testilääke",
        purchase_date="2027-01-01",
        pieces_bought=30,
        dose_per_dosing_day=1,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    for minute in ("14:30", "14:31", "14:32"):
        save(announcement_app, client, due_local=f"2027-01-15T{minute}", text=f"Lääke {minute}")
    at(clock, "2027-01-15T12:29:00+00:00")
    before = _health_and_other_records(ctrl)

    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    play_through(service, cast, clock)
    at(clock, "2027-01-15T12:32:00+00:00")
    service.tick()
    items = client.get(f"{API}/occurrences").get_json()["occurrences"]
    by_text = {i["occurrence"]["text"]: i["occurrence"]["id"] for i in items}
    act(announcement_app, client, by_text["Lääke 14:30"], "done")  # delivered
    act(announcement_app, client, by_text["Lääke 14:31"], "stop", expect=202)  # speaking
    act(announcement_app, client, by_text["Lääke 14:32"], "skip")  # waiting its turn
    service.tick()
    act(announcement_app, client, by_text["Lääke 14:31"], "done")

    assert _health_and_other_records(ctrl) == before
    assert [
        i["acknowledgement"]["action"]
        for i in sorted(
            client.get(f"{API}/occurrences").get_json()["occurrences"],
            key=lambda i: i["occurrence"]["due_at"],
        )
    ] == ["done", "done", "skipped"]


def test_root_admin_and_csrf_guard_occurrence_actions(announcement_app, client, clock):
    service = announcement_app.announcement_service
    save(announcement_app, client)
    at(clock, "2027-01-15T12:30:00+00:00")
    service.tick()
    occurrence_id = client.get(f"{API}/occurrences").get_json()["occurrences"][0]["occurrence"][
        "id"
    ]
    anonymous = announcement_app.test_client()
    admin = support.login(announcement_app.test_client(), "admin")
    for action in ("done", "skip", "stop"):
        url = f"{API}/occurrences/{occurrence_id}/{action}"
        assert anonymous.post(url).status_code in (400, 401)
        assert admin.post(url, headers=headers(announcement_app, admin)).status_code == 403
        assert client.post(url).status_code == 400  # no CSRF token
    item = client.get(f"{API}/occurrences/{occurrence_id}").get_json()
    assert item["acknowledgement"] is None
    assert item["occurrence"]["disposition"] is None
    assert item["attempts"][0]["stop_requested_at"] is None
