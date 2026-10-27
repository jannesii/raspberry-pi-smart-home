"""Spoken announcement preview and speaker tests through public operations.

Real Controller, isolated SQLite, real service policy and media; doubles only
for the Piper subprocess and the Cast transport (``announcement_support``).
These tests do not establish that anything was audible on a real JBL.
"""

from __future__ import annotations

import contextlib
import logging

import announcement_support as support
from announcement_support import (
    MEDIA_ORIGIN,
    SimulatedCrash,
    build_service,
    headers,
    login,
    read_samples,
    segments,
)

from app.services.announcements import TransportError

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord


def preview(app, client, **body):
    response = client.post("/api/announcements/previews", json=body, headers=headers(app, client))
    assert response.status_code == 202, response.get_json()
    return response.get_json()["preview"]


def test_preview_is_chime_then_half_second_gap_then_finnish_speech(announcement_app, client):
    synth = announcement_app.announcement_service.synthesizer
    started = preview(announcement_app, client, text="Muista ottaa lääkkeet.")
    assert started["status"] == "preparing"
    assert started["language"] == "fi"

    announcement_app.announcement_service.tick()

    ready = client.get(f"/api/announcements/previews/{started['id']}").get_json()["preview"]
    assert ready["status"] == "ready"
    assert ready["voice"] == "fi_FI-test-medium"
    audio = client.get(ready["audio_url"])
    assert audio.status_code == 200
    assert audio.mimetype == "audio/wav"
    rate, samples = read_samples(audio.data)
    assert rate == 22050
    parts = segments(samples, rate)
    assert 0.3 <= parts["chime_s"] <= 1.5
    assert abs(parts["gap_s"] - 0.5) < 0.02
    assert len(parts["speech"]) == 22050  # the synthesizer's full 1.0 s utterance
    assert synth.calls == [("Muista ottaa lääkkeet.", "fi")]


def ready_preview(app, client, **body):
    started = preview(app, client, **body)
    app.announcement_service.tick()
    ready = client.get(f"/api/announcements/previews/{started['id']}").get_json()["preview"]
    assert ready["status"] == "ready", ready
    return ready, client.get(ready["audio_url"]).data


def test_english_selection_and_chime_loudness_shape_the_prepared_audio(
    announcement_app, client, doubles
):
    synth, cast = doubles
    soft, soft_audio = ready_preview(
        announcement_app, client, text="Measure blood pressure.", language="en", chime_percent=20
    )
    loud, loud_audio = ready_preview(
        announcement_app, client, text="Measure blood pressure.", language="en", chime_percent=80
    )
    assert soft["voice"] == loud["voice"] == "en_GB-test-medium"
    soft_parts = segments(read_samples(soft_audio)[1], 22050)
    loud_parts = segments(read_samples(loud_audio)[1], 22050)
    assert 3.5 < loud_parts["chime_peak"] / soft_parts["chime_peak"] < 4.5
    assert soft_parts["speech"] == loud_parts["speech"]
    assert len(soft_parts["speech"]) == int(0.6 * 22050)
    # Same wording again reuses the prepared asset instead of re-synthesizing.
    ready_preview(
        announcement_app, client, text="Measure blood pressure.", language="en", chime_percent=80
    )
    assert synth.calls == [("Measure blood pressure.", "en")] * 2
    assert cast.effects == []  # a browser preview never reaches a speaker


def test_preview_and_test_inputs_are_validated_without_truncation(announcement_app, client):
    auth = headers(announcement_app, client)
    cases = [
        ("/api/announcements/previews", {"text": "x" * 501}, "text"),
        ("/api/announcements/previews", {"text": "  "}, "text"),
        ("/api/announcements/previews", {"text": "Hei", "language": "sv"}, "language"),
        ("/api/announcements/previews", {"text": "Hei", "chime_percent": 101}, "chime_percent"),
        ("/api/announcements/tests", {"text": "Hei", "target": "kitchen"}, "target"),
        (
            "/api/announcements/tests",
            {"text": "Hei", "target": "jbl-left", "volume_percent": 12.5},
            "volume_percent",
        ),
    ]
    for url, body, field in cases:
        response = client.post(url, json=body, headers=auth)
        assert response.status_code == 400, (body, response.get_json())
        assert response.get_json()["field"] == field
    assert "500" in client.post(cases[0][0], json=cases[0][1], headers=auth).get_json()["message"]
    assert client.get("/api/announcements/tests").get_json()["tests"] == []


def request_test(app, client, **body):
    payload = {"text": "Muista ottaa lääkkeet.", "target": "jbl-right", **body}
    response = client.post("/api/announcements/tests", json=payload, headers=headers(app, client))
    assert response.status_code == 202, response.get_json()
    return response.get_json()


def attempt_of(client, test_id):
    return client.get(f"/api/announcements/tests/{test_id}").get_json()["attempt"]


def test_speaker_test_plays_through_cookie_free_capability_and_restores_volume(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    created = request_test(announcement_app, client, volume_percent=55, chime_percent=30)
    assert created["kind"] == "speaker_test"
    assert created["test"]["target_key"] == "jbl-right"
    assert created["attempt"]["status"] == "requested"
    assert cast.effects == []

    clock[0] += 1
    service.tick()
    attempt = attempt_of(client, created["test"]["id"])
    assert attempt["status"] == "dispatched"
    assert attempt["intent_at"] and attempt["dispatched_at"]
    assert attempt["target_device_id"] == "uuid-right"
    assert attempt["volume_before"] == 0.4 and attempt["volume_applied"] == 0.55
    assert attempt["evidence_scope"] == "device"
    assert cast.effects == [("set_volume", "jbl-right", 0.55), ("play", "jbl-right", attempt["id"])]
    url = cast.plays[0]["url"]
    assert url.startswith(MEDIA_ORIGIN + "/announcement-media/")

    speaker = announcement_app.test_client()  # no session cookie at all
    media = speaker.get(url.removeprefix(MEDIA_ORIGIN))
    assert media.status_code == 200
    assert media.mimetype == "audio/wav"
    assert "Set-Cookie" not in media.headers
    assert media.headers["Cache-Control"] == "no-store"
    rate, samples = read_samples(media.data)
    assert abs(segments(samples, rate)["gap_s"] - 0.5) < 0.02
    ranged = speaker.get(url.removeprefix(MEDIA_ORIGIN), headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.data) == 100
    # The capability grants nothing beyond this one asset.
    assert speaker.get("/api/announcements/tests").status_code == 401
    token = url.rsplit("/", 1)[1].removesuffix(".wav")
    assert speaker.get(f"/api/announcements/tests/{token}").status_code == 401

    cast.report("192.168.1.119", attempt["id"], "playing")
    clock[0] += 1
    service.tick()
    playing = attempt_of(client, created["test"]["id"])
    assert playing["status"] == "playing"
    assert playing["playback_observed_at"] == "2027-01-15T12:00:02+00:00"

    cast.report("192.168.1.119", attempt["id"], "finished")
    clock[0] += 5
    service.tick()
    done = attempt_of(client, created["test"]["id"])
    assert done["status"] == "finished"
    assert done["reason"] is None
    assert done["finished_at"] == "2027-01-15T12:00:07+00:00"
    assert done["volume_restore"] == "restored"
    assert cast.speakers["192.168.1.119"].volume == 0.4
    assert speaker.get(url.removeprefix(MEDIA_ORIGIN)).status_code == 404  # revoked at the end


def test_media_capability_expires_and_rejects_unknown_tokens(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    request_test(announcement_app, client)
    announcement_app.announcement_service.tick()
    path = cast.plays[0]["url"].removeprefix(MEDIA_ORIGIN)
    speaker = announcement_app.test_client()
    assert speaker.get(path).status_code == 200
    assert speaker.get("/announcement-media/not-a-real-token.wav").status_code == 404
    assert speaker.get(path[:-6] + "AAAA.wav").status_code == 404
    clock[0] += 600  # the configured playback window
    assert speaker.get(path).status_code == 404


def test_volume_is_left_alone_when_the_owner_changed_it_during_the_test(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    created = request_test(announcement_app, client, volume_percent=60)
    service = announcement_app.announcement_service
    service.tick()
    attempt = attempt_of(client, created["test"]["id"])
    cast.report("192.168.1.119", attempt["id"], "playing")
    service.tick()
    cast.speakers["192.168.1.119"].volume = 0.25  # owner turned it down meanwhile
    cast.report("192.168.1.119", attempt["id"], "finished")
    service.tick()
    done = attempt_of(client, created["test"]["id"])
    assert done["status"] == "finished"
    assert done["volume_restore"] == "skipped_newer_intent"
    assert cast.speakers["192.168.1.119"].volume == 0.25
    assert ("set_volume", "jbl-right", 0.4) not in cast.effects


def test_speaker_tests_only_play_on_an_idle_speaker(announcement_app, client, doubles):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    reasons = {}
    for state in ("playing", "unknown", "unavailable"):
        cast.speakers["192.168.1.148"].state = state
        created = request_test(announcement_app, client, target="jbl-left")
        service.tick()
        attempt = attempt_of(client, created["test"]["id"])
        assert attempt["status"] == "failed"
        assert attempt["intent_at"] is None  # nothing was sent
        reasons[state] = attempt["reason"]
    assert reasons == {
        "playing": "speaker_playing",
        "unknown": "speaker_state_unknown",
        "unavailable": "speaker_unavailable",
    }
    assert cast.effects == []


def test_tests_on_the_same_speaker_serialize_while_others_proceed(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    first = request_test(announcement_app, client, text="Ensimmäinen")
    clock[0] += 1
    second = request_test(announcement_app, client, text="Toinen")
    left = request_test(announcement_app, client, text="Vasen", target="jbl-left")
    service.tick()
    assert attempt_of(client, first["test"]["id"])["status"] == "dispatched"
    assert attempt_of(client, second["test"]["id"])["status"] == "requested"
    assert attempt_of(client, left["test"]["id"])["status"] == "dispatched"
    cast.speakers["192.168.1.119"].state = "playing"  # ours, still speaking
    service.tick()
    assert attempt_of(client, second["test"]["id"])["status"] == "requested"
    cast.report("192.168.1.119", attempt_of(client, first["test"]["id"])["id"], "finished")
    service.tick()
    assert attempt_of(client, first["test"]["id"])["status"] == "finished"
    assert attempt_of(client, second["test"]["id"])["status"] == "dispatched"
    plays = [effect[1:] for effect in cast.effects if effect[0] == "play"]
    assert [p[0] for p in plays] == ["jbl-right", "jbl-left", "jbl-right"]


def test_queued_test_that_waits_too_long_does_not_play_late(
    announcement_app, client, doubles, clock
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    first = request_test(announcement_app, client)
    clock[0] += 1  # queue order is request time
    second = request_test(announcement_app, client, text="Myöhässä")
    service.tick()
    clock[0] += 120
    cast.report("192.168.1.119", attempt_of(client, first["test"]["id"])["id"], "finished")
    service.tick()
    late = attempt_of(client, second["test"]["id"])
    assert late["status"] == "failed"
    assert late["reason"] == "queue_expired"
    assert len(cast.plays) == 1


def test_stop_is_requested_then_observed(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    created = request_test(announcement_app, client)
    test_id = created["test"]["id"]
    service.tick()
    attempt = attempt_of(client, test_id)
    cast.report("192.168.1.119", attempt["id"], "playing")
    service.tick()

    cast.stop_result = "failed"
    clock[0] += 1
    stop = client.post(
        f"/api/announcements/tests/{test_id}/stop", headers=headers(announcement_app, client)
    )
    assert stop.status_code == 202
    assert stop.get_json()["attempt"]["stop_requested_at"] == "2027-01-15T12:00:01+00:00"
    assert stop.get_json()["attempt"]["stop_outcome"] is None
    service.tick()
    failed_stop = attempt_of(client, test_id)
    assert failed_stop["status"] == "playing"
    assert failed_stop["stop_outcome"] == "failed"

    cast.stop_result = "stopped"
    clock[0] += 1
    client.post(
        f"/api/announcements/tests/{test_id}/stop", headers=headers(announcement_app, client)
    )
    service.tick()
    stopped = attempt_of(client, test_id)
    assert stopped["status"] == "stopped"
    assert stopped["stop_outcome"] == "stopped"
    assert stopped["stop_observed_at"] == "2027-01-15T12:00:02+00:00"
    assert stopped["volume_restore"] == "restored"
    assert cast.speakers["192.168.1.119"].volume == 0.4
    again = client.post(
        f"/api/announcements/tests/{test_id}/stop", headers=headers(announcement_app, client)
    )
    assert again.status_code == 409


def test_stop_before_dispatch_cancels_without_touching_the_speaker(
    announcement_app, client, doubles
):
    _synth, cast = doubles
    created = request_test(announcement_app, client)
    test_id = created["test"]["id"]
    response = client.post(
        f"/api/announcements/tests/{test_id}/stop", headers=headers(announcement_app, client)
    )
    assert response.status_code == 202
    announcement_app.announcement_service.tick()
    attempt = attempt_of(client, test_id)
    assert attempt["status"] == "stopped"
    assert attempt["reason"] == "cancelled_before_dispatch"
    assert attempt["stop_outcome"] == "not_dispatched"
    assert cast.effects == []


def test_uncertain_dispatch_is_unknown_and_never_replayed_after_restart(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    created = request_test(announcement_app, client)
    cast.play_crash = True
    with contextlib.suppress(SimulatedCrash):
        announcement_app.announcement_service.tick()
    assert attempt_of(client, created["test"]["id"])["status"] == "dispatching"

    cast.play_crash = False
    queued = request_test(announcement_app, client, target="jbl-left", text="Odottaa")
    clock[0] += 30
    restarted = build_service(announcement_app.ctrl, clock, doubles, tmp_path / "cache")
    announcement_app.announcement_service = restarted
    restarted.tick()
    restarted.tick()
    unknown = attempt_of(client, created["test"]["id"])
    assert unknown["status"] == "unknown"
    assert unknown["reason"] == "interrupted_by_restart"
    assert unknown["intent_at"] is not None
    assert unknown["volume_restore"] == "unknown"
    assert [p["target"] for p in cast.plays] == ["jbl-right", "jbl-left"]
    # Work that never reached intent is still processed after the restart.
    assert attempt_of(client, queued["test"]["id"])["status"] == "dispatched"


def test_transport_failures_keep_certain_and_uncertain_outcomes_apart(
    announcement_app, client, doubles
):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    cast.play_error = TransportError("connection_refused", certain_not_played=True)
    refused = request_test(announcement_app, client)
    service.tick()
    cast.play_error = TransportError("load_uncertain", certain_not_played=False)
    uncertain = request_test(announcement_app, client, target="jbl-left")
    service.tick()
    assert attempt_of(client, refused["test"]["id"])["status"] == "failed"
    assert attempt_of(client, refused["test"]["id"])["reason"] == "connection_refused"
    assert attempt_of(client, uncertain["test"]["id"])["status"] == "unknown"
    assert attempt_of(client, uncertain["test"]["id"])["reason"] == "load_uncertain"


def test_playback_without_evidence_becomes_unknown(announcement_app, client, clock):
    service = announcement_app.announcement_service
    created = request_test(announcement_app, client)
    service.tick()
    clock[0] += 31
    service.tick()
    attempt = attempt_of(client, created["test"]["id"])
    assert attempt["status"] == "unknown"
    assert attempt["reason"] == "no_playback_evidence"
    assert attempt["playback_observed_at"] is None


def test_repeated_test_request_is_idempotent(announcement_app, client, doubles):
    auth = headers(announcement_app, client)
    body = {"text": "Hei", "target": "jbl-left", "request_id": "browser-request-0001"}
    first = client.post("/api/announcements/tests", json=body, headers=auth)
    again = client.post("/api/announcements/tests", json=body, headers=auth)
    assert first.status_code == 202 and again.status_code == 200
    assert again.get_json()["test"]["id"] == first.get_json()["test"]["id"]
    changed = client.post("/api/announcements/tests", json={**body, "text": "Toinen"}, headers=auth)
    assert changed.status_code == 409
    assert len(client.get("/api/announcements/tests").get_json()["tests"]) == 1


def test_speaker_test_cannot_acknowledge_or_schedule(announcement_app, client):
    created = request_test(announcement_app, client)
    test_id = created["test"]["id"]
    auth = headers(announcement_app, client)
    for action in ("done", "skip", "snooze", "acknowledge"):
        assert client.post(
            f"/api/announcements/tests/{test_id}/{action}", headers=auth
        ).status_code in (404, 405)
    assert "acknowledged_at" not in created["test"]
    assert created["attempt"]["speaker_test_id"] == test_id


def test_missing_integrations_are_reported_and_fail_tests_visibly(
    announcement_app, client, doubles, clock, tmp_path
):
    _synth, cast = doubles
    announcement_app.announcement_service = build_service(
        announcement_app.ctrl, clock, doubles, tmp_path / "cache", media_origin=None
    )
    status = client.get("/api/announcements/status").get_json()
    assert status["media_origin"] == {"configured": False, "origin": None}
    assert [t["name"] for t in status["targets"]] == ["Jbl-Right", "Jbl-Left", "Both JBLs (group)"]
    assert [t["host"] for t in status["targets"]] == ["192.168.1.119", "192.168.1.148", None]
    created = request_test(announcement_app, client)
    announcement_app.announcement_service.tick()
    assert attempt_of(client, created["test"]["id"])["reason"] == "media_origin_not_configured"

    announcement_app.announcement_service = build_service(
        announcement_app.ctrl, clock, doubles, tmp_path / "cache"
    )
    cast.available = False
    assert client.get("/api/announcements/status").get_json()["cast"]["available"] is False
    created = request_test(announcement_app, client)
    announcement_app.announcement_service.tick()
    assert attempt_of(client, created["test"]["id"])["reason"] == "cast_unavailable"
    assert cast.effects == []

    announcement_app.announcement_service = None
    unavailable = client.get("/api/announcements/status")
    assert unavailable.status_code == 503
    assert "not running" in unavailable.get_json()["message"]


def test_synthesis_failure_is_visible_and_nothing_is_sent(announcement_app, client, doubles):
    synth, cast = doubles
    synth.failure = "synthesis_failed"
    started = preview(announcement_app, client, text="Hei")
    created = request_test(announcement_app, client, text="Hei taas")
    announcement_app.announcement_service.tick()
    failed = client.get(f"/api/announcements/previews/{started['id']}").get_json()["preview"]
    assert failed["status"] == "failed" and failed["reason"] == "synthesis_failed"
    assert "audio_url" not in failed
    assert attempt_of(client, created["test"]["id"])["reason"] == "synthesis_failed"
    assert cast.effects == []


def test_configured_device_identity_must_match(announcement_app, client, doubles, clock, tmp_path):
    from app.services.announcements import SpeakerTarget

    _synth, cast = doubles
    targets = (
        SpeakerTarget(key="jbl-right", name="Jbl-Right", host="192.168.1.119", device_id="other"),
        SpeakerTarget(key="jbl-left", name="Jbl-Left", host="192.168.1.148"),
    )
    announcement_app.announcement_service = build_service(
        announcement_app.ctrl, clock, doubles, tmp_path / "cache", targets=targets
    )
    created = request_test(announcement_app, client)
    announcement_app.announcement_service.tick()
    attempt = attempt_of(client, created["test"]["id"])
    assert attempt["reason"] == "device_identity_mismatch"
    assert cast.effects == []


def test_cache_eviction_keeps_test_history(announcement_app, client, doubles, clock):
    _synth, cast = doubles
    service = announcement_app.announcement_service
    created = request_test(announcement_app, client)
    service.tick()
    attempt = attempt_of(client, created["test"]["id"])
    cast.report("192.168.1.119", attempt["id"], "finished")
    service.tick()
    assert client.get("/api/announcements/status").get_json()["cache"]["entries"] == 1
    clock[0] += 8 * 24 * 3600
    service.tick()
    assert client.get("/api/announcements/status").get_json()["cache"]["entries"] == 0
    kept = attempt_of(client, created["test"]["id"])
    assert kept["status"] == "finished"
    assert kept["media_key"] == attempt["media_key"]
    listed = client.get("/api/announcements/tests").get_json()["tests"]
    assert listed[0]["test"]["text"] == "Muista ottaa lääkkeet."


def test_routine_logs_carry_no_capability_token_or_reminder_text(
    announcement_app, client, doubles, caplog
):
    from app.security import apply_security_headers

    apply_security_headers(announcement_app)  # its debug log names request paths
    _synth, cast = doubles
    caplog.set_level(logging.DEBUG)
    created = request_test(announcement_app, client, text="Salainen muistutus 4711")
    service = announcement_app.announcement_service
    service.tick()
    url = cast.plays[0]["url"]
    announcement_app.test_client().get(url.removeprefix(MEDIA_ORIGIN))
    announcement_app.test_client().get("/announcement-media/wrong.wav")
    cast.report("192.168.1.119", attempt_of(client, created["test"]["id"])["id"], "finished")
    service.tick()
    token = url.rsplit("/", 1)[1].removesuffix(".wav")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "dispatched" in logged
    assert "/announcement-media/<redacted>" in logged
    assert token not in logged
    assert "Salainen muistutus" not in logged


def test_root_admin_and_csrf_guard_every_announcement_operation(announcement_app, client):
    created = request_test(announcement_app, client)
    test_id = created["test"]["id"]
    reads = [
        "/api/announcements/status",
        "/api/announcements/tests",
        f"/api/announcements/tests/{test_id}",
    ]
    writes = [
        ("/api/announcements/previews", {"text": "Hei"}),
        ("/api/announcements/tests", {"text": "Hei", "target": "jbl-left"}),
        (f"/api/announcements/tests/{test_id}/stop", {}),
    ]
    anonymous = announcement_app.test_client()
    for url in reads:
        assert anonymous.get(url).status_code == 401
    for url, body in writes:
        assert anonymous.post(url, json=body).status_code in (400, 401)
    admin = login(announcement_app.test_client(), "admin")
    for url in reads:
        assert admin.get(url).status_code == 403
    for url, body in writes:
        response = admin.post(url, json=body, headers=headers(announcement_app, admin))
        assert response.status_code == 403
    for url, body in writes:
        assert client.post(url, json=body).status_code == 400  # missing CSRF token
    assert attempt_of(client, test_id)["status"] == "requested"
    assert anonymous.get("/settings/announcements").status_code == 401
    assert admin.get("/settings/announcements").status_code == 302


def test_page_is_reachable_from_settings_for_root_admin_only(announcement_app, client):
    page = client.get("/settings/announcements")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="announcementPreviewButton"' in html
    assert 'id="announcementTestButton"' in html
    assert "js/announcements.js" in html and "?v=" in html
    assert "Announcements" in client.get("/settings").get_data(as_text=True)
    admin = login(announcement_app.test_client(), "admin")
    assert "Announcements" not in admin.get("/settings").get_data(as_text=True)
