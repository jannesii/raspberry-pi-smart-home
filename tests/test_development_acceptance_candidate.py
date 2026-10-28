"""Run in a combined checkout with the actual announcement candidate.

Transport and synthesis are external substitutes; Controller, authentication,
service construction, target policy, capability lifetime and HTTP are real.
"""

import functools
import json
import os
from urllib.parse import urlsplit

import pytest
import requests
from test_development_launch import (  # noqa: F401
    capture_server,
    dev_browser,
    postgres_socket_dir,
    redis_pair,
    running_dev,
)

support = pytest.importorskip(
    "announcement_support", reason="requires temporary combined announcement candidate"
)
announcement_app = support.announcement_app
clock = support.clock
doubles = support.doubles
discord = support.discord


@pytest.fixture
def accepted_app(request, monkeypatch, tmp_path):
    from test_development_launch import free_port

    import app.services.announcements as candidate
    import app.services.announcements.synthesis as synthesis
    import app.services.announcements.transport as transport
    from app import _register_environment_context
    from app.development_acceptance import initialize

    app = request.getfixturevalue("announcement_app")
    controlled_clock = request.getfixturevalue("clock")
    synth, cast = request.getfixturevalue("doubles")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "DEV_ACCEPTANCE",
        json.dumps(
            {
                "speakers": [
                    {"key": "jbl-right", "name": "Acceptance right", "host": "192.168.1.119"},
                ]
            }
        ),
    )
    if getattr(request, "param", False):
        chosen = json.loads(os.environ["DEV_ACCEPTANCE"])
        chosen["discord"] = {
            "label": "Acceptance",
            "webhook": "https://discord.com/api/webhooks/123/selected-secret",
        }
        monkeypatch.setenv("DEV_ACCEPTANCE", json.dumps(chosen))
    from app.services import alert_webhook

    monkeypatch.setattr(alert_webhook.requests, "post", request.getfixturevalue("discord").post)
    monkeypatch.setenv("DEV_ACCEPTANCE_MEDIA_ORIGIN", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://discord.com/api/webhooks/123/inherited-secret")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "inherited-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "inherited-secret")
    monkeypatch.setattr(synthesis, "PiperSubprocessSynthesizer", lambda **kw: synth)
    monkeypatch.setattr(transport, "PyChromecastTransport", lambda: cast)
    # Time is a controlled external boundary; the real candidate is constructed.
    monkeypatch.setattr(
        candidate,
        "AnnouncementService",
        functools.partial(
            candidate.AnnouncementService,
            clock=lambda: controlled_clock[0],
        ),
    )
    _register_environment_context(app)
    service = initialize(app)
    # Process deterministically through HTTP + explicit tick, as candidate tests do.
    service.stop()
    service._thread.join(timeout=3)
    try:
        yield app, cast, controlled_clock
    finally:
        service.stop()
        app.development_media_server.shutdown()
        app.development_media_server.server_close()


def test_actual_candidate_only_dispatches_selected_speaker_and_serves_lan_capability(accepted_app):
    app, cast, time = accepted_app
    client = support.login(app.test_client())
    auth = support.headers(app, client)
    refused = client.post(
        "/api/announcements/tests", json={"text": "Test", "target": "jbl-left"}, headers=auth
    )
    assert refused.status_code == 400
    sent = client.post(
        "/api/announcements/tests", json={"text": "Test", "target": "jbl-right"}, headers=auth
    )
    assert sent.status_code == 202, sent.get_json()
    app.announcement_service.tick()
    assert len(cast.plays) == 1
    assert cast.plays[0]["target"] == "jbl-right"
    url = cast.plays[0]["url"]
    origin = urlsplit(url)
    assert origin.hostname == "127.0.0.1"
    fetched = requests.get(url, timeout=3)
    assert fetched.status_code == 200 and fetched.headers["Content-Type"].startswith("audio/wav")
    assert "Set-Cookie" not in fetched.headers
    base = f"{origin.scheme}://{origin.netloc}"
    for path in (
        "/login",
        "/settings/announcements",
        "/api/announcements/status",
        "/announcement-media/invalid.wav",
    ):
        assert requests.get(base + path, timeout=3).status_code == 404
    time[0] += 121
    assert requests.get(url, timeout=3).status_code == 404
    status = client.get("/api/announcements/status")
    assert status.status_code == 200
    assert "inherited-secret" not in status.get_data(as_text=True)
    assert app.announcement_service.fallback_channel.status()["available"] is False
    assert app.announcement_service.spotify.configured is False
    assert all(effect[1] == "jbl-right" for effect in cast.effects)


def test_combined_operator_startup_keeps_candidate_default_builder_off(request):
    browser = request.getfixturevalue("dev_browser")
    runtime = request.getfixturevalue("running_dev")
    response = browser.request("GET", "/api/announcements/status")
    assert response.status_code == 503, response.text
    assert response.json()["error"] == "service_unavailable"
    assert "Announcement service disabled by ANNOUNCEMENTS_ENABLED" in runtime["log"].read_text()


@pytest.mark.parametrize("accepted_app", [False, True], indirect=True)
def test_only_explicit_discord_destination_receives_fallback(accepted_app, request):
    app, cast, time = accepted_app
    client = support.login(app.test_client())
    synth, _ = request.getfixturevalue("doubles")
    synth.failure = "synthesis_failed"
    result = client.post(
        "/api/announcements/schedules",
        json={
            "text": "Acceptance reminder",
            "target": "jbl-right",
            "due_local": "2027-01-15T14:30",
        },
        headers=support.headers(app, client),
    )
    assert result.status_code == 201, result.get_json()
    time[0] += 1800
    app.announcement_service.tick()
    recorded = request.getfixturevalue("discord").requests
    selected = app.announcement_service.fallback_channel.configured
    assert len(recorded) == (1 if selected else 0)
    if selected:
        assert recorded[0]["url"] == "https://discord.com/api/webhooks/123/selected-secret"
    assert cast.effects == []
