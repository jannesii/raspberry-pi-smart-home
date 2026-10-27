"""Optional account lifecycle and interruption policy through authenticated APIs."""

import announcement_support as support
from announcement_support import headers

announcement_app = support.announcement_app
client = support.client
clock = support.clock
doubles = support.doubles
discord = support.discord


def test_disconnected_account_is_optional_and_secrets_are_never_public(announcement_app, client):
    response = client.get("/api/announcements/spotify")
    assert response.status_code == 200
    assert response.json["spotify"]["connected"] is False
    assert response.json["spotify"]["enabled"] is False
    assert response.json["spotify"]["reason"] == "not_configured"
    assert (
        client.post(
            "/api/announcements/spotify/enable",
            json={"enabled": True},
            headers=headers(announcement_app, client),
        ).status_code
        == 409
    )


class Response:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class SpotifyServer:
    """Deterministic external HTTP server: real OAuth/adapter/service stay in path."""

    def __init__(self):
        import copy

        self.copy = copy.deepcopy
        self.calls = []
        self.product = "premium"
        self.fail_resume = False
        self.refresh_error = False
        self.during_pause = None
        self.player = {
            "is_playing": True,
            "progress_ms": 12000,
            "timestamp": 1000,
            "item": {"uri": "spotify:track:original"},
            "context": {"uri": "spotify:album:original"},
            "device": {"id": "spotify-right", "volume_percent": 40},
        }

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, self.copy(kwargs)))
        if url.endswith("/api/token"):
            if self.refresh_error:
                return Response(status=400)
            return Response(
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": 3600,
                    "scope": "user-read-playback-state user-modify-playback-state user-read-private",
                }
            )
        if url.endswith("/me/player/devices"):
            return Response(
                {"devices": [{"id": "spotify-right", "name": "Jbl-Right", "is_restricted": False}]}
            )
        if url.endswith("/me"):
            return Response({"product": self.product})
        if method == "GET":
            return Response(self.copy(self.player), 200 if self.player else 204)
        if url.endswith("/pause"):
            if self.during_pause:
                self.during_pause()
            self.player["is_playing"] = False
            self.player["timestamp"] = 1100
        if url.endswith("/play"):
            if self.fail_resume:
                return Response(status=403)
            self.player["is_playing"] = True
        return Response(status=204)


def setup_spotify(app, clock):
    from cryptography.fernet import Fernet

    from app.services.announcements.spotify import SpotifyHTTP, SpotifyRestoration

    server = SpotifyServer()
    app.announcement_service.spotify = SpotifyRestoration(
        app.ctrl,
        key=Fernet.generate_key(),
        clock=lambda: clock[0],
        http=SpotifyHTTP(
            "client-id",
            "client-secret",
            "https://home.test/api/announcements/spotify/callback",
            request=server.request,
        ),
    )
    return server


def wait_settings(client):
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get("/api/announcements/spotify").json["spotify"]
        if status["operation"] is None:
            return status
        time.sleep(0.01)
    raise AssertionError("Spotify settings operation did not settle")


def connect(app, client):
    from urllib.parse import parse_qs, urlsplit

    response = client.post("/api/announcements/spotify/connect", headers=headers(app, client))
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json["authorization_url"]).query)["state"][0]
    response = client.get(
        "/api/announcements/spotify/callback", query_string={"code": "code-secret", "state": state}
    )
    assert response.status_code == 302
    wait_settings(client)
    return state


def test_root_can_connect_enable_and_disconnect_without_exposing_tokens(
    announcement_app, client, clock
):
    server = setup_spotify(announcement_app, clock)
    state = connect(announcement_app, client)
    status = client.get("/api/announcements/spotify").json["spotify"]
    assert status["connected"] is True and status["enabled"] is False
    assert "secret" not in str(status)
    assert (
        client.get(
            "/api/announcements/spotify/callback", query_string={"code": "again", "state": state}
        ).status_code
        == 400
    )
    client.post(
        "/api/announcements/spotify/enable",
        json={"enabled": True, "devices": {"jbl-right": "spotify-right"}},
        headers=headers(announcement_app, client),
    )
    assert wait_settings(client)["enabled"] is True
    # Public settings survive service reload with the same external key.
    from app.services.announcements.spotify import SpotifyRestoration

    current = announcement_app.announcement_service.spotify
    replacement = SpotifyRestoration(
        announcement_app.ctrl, http=current.http, clock=lambda: clock[0]
    )
    replacement.cipher = current.cipher
    announcement_app.announcement_service.spotify = replacement
    assert client.get("/api/announcements/spotify").json["spotify"]["enabled"] is True
    assert (
        client.post(
            "/api/announcements/spotify/disconnect", headers=headers(announcement_app, client)
        ).json["spotify"]["connected"]
        is False
    )
    assert not any(url.endswith("/play") for _, url, _ in server.calls)


def enable(app, client):
    connect(app, client)
    response = client.post(
        "/api/announcements/spotify/enable",
        json={"enabled": True, "devices": {"jbl-right": "spotify-right"}},
        headers=headers(app, client),
    )
    assert response.status_code == 200
    wait_settings(client)


def announce(app, client, clock, cast, *, urgent=True):
    response = client.post(
        "/api/announcements/schedules",
        json={
            "text": "Muista.",
            "target": "jbl-right",
            "due_local": "2027-01-15T14:01",
            "urgent": urgent,
        },
        headers=headers(app, client),
    )
    assert response.status_code == 201
    cast.speakers["192.168.1.119"].state = "playing"
    clock[0] += 60
    app.announcement_service.tick()
    return client.get("/api/announcements/occurrences").json["occurrences"][0]


def test_urgent_interruption_restores_original_context_without_acknowledgement(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    attempt = detail["attempts"][0]
    assert attempt["media_restore"] == "pending"
    cast.report("192.168.1.119", attempt["id"], "playing")
    announcement_app.announcement_service.tick()
    cast.report("192.168.1.119", attempt["id"], "finished")
    announcement_app.announcement_service.tick()
    detail = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert detail["attempts"][0]["media_restore"] == "restored"
    assert detail["attempts"][0]["volume_restore"] == "restored"
    assert detail["occurrence"]["delivery"] == "delivered"
    assert detail["acknowledgement"] is None
    resume = next(c for c in server.calls if c[1].endswith("/play"))
    assert resume[2]["json"] == {
        "context_uri": "spotify:album:original",
        "offset": {"uri": "spotify:track:original"},
        "position_ms": 12000,
    }


import pytest


@pytest.mark.parametrize(
    "change",
    [
        "track",
        "device",
        "progress",
        "timestamp",
        "spotify-volume",
        "cast-volume",
        "cast-identity",
        "unknown",
    ],
)
def test_newer_owner_intent_or_unknown_state_prevents_restoration(
    announcement_app, client, clock, doubles, change
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    attempt_id = detail["attempts"][0]["id"]
    cast.report("192.168.1.119", attempt_id, "playing")
    announcement_app.announcement_service.tick()
    if change == "track":
        server.player["item"]["uri"] = "spotify:track:new"
    elif change == "device":
        server.player["device"]["id"] = "owner-phone"
    elif change == "progress":
        server.player["progress_ms"] = 19000
    elif change == "timestamp":
        server.player["timestamp"] = 2000
    elif change == "spotify-volume":
        server.player["device"]["volume_percent"] = 65
    elif change == "cast-volume":
        cast.speakers["192.168.1.119"].volume = 0.65
    elif change == "cast-identity":
        cast.speakers["192.168.1.119"].device_id = "new-device"
    else:
        server.player = None
    cast.report("192.168.1.119", attempt_id, "finished")
    announcement_app.announcement_service.tick()
    item = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert item["attempts"][0]["media_restore"] in ("skipped_newer_intent", "unknown")
    assert not any(url.endswith("/play") for _, url, _ in server.calls)
    assert item["occurrence"]["delivery"] == "delivered"


@pytest.mark.parametrize("ending", ["failed", "stop", "lost", "interrupted"])
def test_restoration_ownership_applies_on_failures_and_stop(
    announcement_app, client, clock, doubles, ending
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    attempt_id = detail["attempts"][0]["id"]
    cast.report("192.168.1.119", attempt_id, "playing")
    announcement_app.announcement_service.tick()
    if ending == "stop":
        assert (
            client.post(
                "/api/announcements/occurrences/" + detail["occurrence"]["id"] + "/stop",
                headers=headers(announcement_app, client),
            ).status_code
            == 202
        )
    else:
        cast.report("192.168.1.119", attempt_id, ending)
    announcement_app.announcement_service.tick()
    item = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    expected = "restored" if ending in ("failed", "stop") else "skipped_newer_intent"
    assert item["attempts"][0]["media_restore"] == expected
    assert item["acknowledgement"] is None
    assert any(url.endswith("/play") for _, url, _ in server.calls) == (expected == "restored")


def test_restore_failure_is_separate_from_successful_delivery(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    attempt_id = detail["attempts"][0]["id"]
    cast.report("192.168.1.119", attempt_id, "playing")
    announcement_app.announcement_service.tick()
    server.fail_resume = True
    cast.report("192.168.1.119", attempt_id, "finished")
    announcement_app.announcement_service.tick()
    item = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert item["attempts"][0]["media_restore"] == "failed"
    assert item["occurrence"]["delivery"] == "delivered"
    assert item["acknowledgement"] is None
    assert client.get("/api/announcements/spotify").json["spotify"]["reason"] == "access_denied"


def test_newer_spotify_volume_does_not_get_overwritten_by_cast_volume_restore(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    attempt_id = detail["attempts"][0]["id"]
    server.player["device"]["volume_percent"] = 65
    cast.report("192.168.1.119", attempt_id, "finished")
    announcement_app.announcement_service.tick()
    item = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert item["attempts"][0]["volume_restore"] == "skipped_newer_intent"
    assert cast.speakers["192.168.1.119"].volume == 0.3


@pytest.mark.parametrize("user", [None, "admin"])
def test_spotify_controls_require_root_admin(announcement_app, clock, user):
    setup_spotify(announcement_app, clock)
    c = announcement_app.test_client()
    if user:
        support.login(c, user)
    for path, method in [
        ("", "get"),
        ("/connect", "post"),
        ("/enable", "post"),
        ("/disconnect", "post"),
        ("/callback", "get"),
    ]:
        response = getattr(c, method)(
            "/api/announcements/spotify" + path, headers=headers(announcement_app, c)
        )
        assert response.status_code in (302, 401, 403)


@pytest.mark.parametrize("action", ["connect", "enable", "disconnect"])
def test_spotify_mutations_require_csrf(announcement_app, client, action):
    assert (
        client.post("/api/announcements/spotify/" + action, json={"enabled": True}).status_code
        == 400
    )


def test_invalid_oauth_state_never_exchanges_a_code(announcement_app, client, clock):
    server = setup_spotify(announcement_app, clock)
    client.post("/api/announcements/spotify/connect", headers=headers(announcement_app, client))
    response = client.get(
        "/api/announcements/spotify/callback",
        query_string={"code": "code-secret", "state": "wrong-state"},
    )
    assert response.status_code == 400
    assert server.calls == []


def test_non_premium_account_stays_disconnected(announcement_app, client, clock):
    from urllib.parse import parse_qs, urlsplit

    server = setup_spotify(announcement_app, clock)
    server.product = "free"
    response = client.post(
        "/api/announcements/spotify/connect", headers=headers(announcement_app, client)
    )
    state = parse_qs(urlsplit(response.json["authorization_url"]).query)["state"][0]
    response = client.get(
        "/api/announcements/spotify/callback", query_string={"code": "code-secret", "state": state}
    )
    assert response.status_code == 302
    assert wait_settings(client)["reason"] == "premium_required"
    assert client.get("/api/announcements/spotify").json["spotify"]["connected"] is False


def test_connected_but_disabled_account_does_not_touch_playback(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    connect(announcement_app, client)
    detail = announce(announcement_app, client, clock, doubles[1])
    assert detail["attempts"][0]["media_restore"] == "disabled"
    assert not any("/me/player" in url for _, url, _ in server.calls)


def test_expired_refresh_access_is_visible_and_does_not_block_basic_speech(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    server.refresh_error = True
    clock[0] += 3600
    response = client.post(
        "/api/announcements/schedules",
        json={
            "text": "Muista.",
            "target": "jbl-right",
            "due_local": "2027-01-15T15:01",
            "urgent": True,
        },
        headers=headers(announcement_app, client),
    )
    assert response.status_code == 201
    doubles[1].speakers["192.168.1.119"].state = "playing"
    clock[0] += 60
    announcement_app.announcement_service.tick()
    assert len(doubles[1].plays) == 1
    assert (
        client.get("/api/announcements/spotify").json["spotify"]["reason"] == "reconnect_required"
    )
    assert not any(url.endswith("/play") for _, url, _ in server.calls)


def test_restart_drops_interruption_ownership_and_never_resumes_music(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    detail = announce(announcement_app, client, clock, doubles[1])
    announcement_app.announcement_service = support.build_service(
        announcement_app.ctrl, clock, doubles, announcement_app.config["ANNOUNCEMENT_CACHE_DIR"]
    )
    item = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert item["attempts"][0]["media_restore"] == "unknown"
    assert item["attempts"][0]["status"] == "unknown"
    assert not any(url.endswith("/play") for _, url, _ in server.calls)


def test_ordinary_reminder_waits_for_spotify_and_debounces_track_transition(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    client.post(
        "/api/announcements/schedules",
        json={"text": "Muista.", "target": "jbl-right", "due_local": "2027-01-15T14:01"},
        headers=headers(announcement_app, client),
    )
    clock[0] += 60
    announcement_app.announcement_service.tick()
    assert cast.plays == []
    assert (
        client.get("/api/announcements/occurrences").json["occurrences"][0]["occurrence"][
            "waiting_reason"
        ]
        == "music_playing"
    )
    server.player["is_playing"] = False
    clock[0] += 5
    announcement_app.announcement_service.tick()
    assert cast.plays == []
    server.player["is_playing"] = True
    server.player["item"]["uri"] = "spotify:track:next"
    clock[0] += 5
    announcement_app.announcement_service.tick()
    assert cast.plays == []
    server.player["is_playing"] = False
    clock[0] += 5
    announcement_app.announcement_service.tick()
    assert cast.plays == []
    clock[0] += 5
    announcement_app.announcement_service.tick()
    assert len(cast.plays) == 1
    assert not any(method == "PUT" for method, _, _ in server.calls)


def test_unreadable_credentials_are_visible_without_attempting_playback(
    announcement_app, client, clock
):
    from cryptography.fernet import Fernet

    server = setup_spotify(announcement_app, clock)
    connect(announcement_app, client)
    announcement_app.announcement_service.spotify.cipher = Fernet(Fernet.generate_key())
    status = client.get("/api/announcements/spotify").json["spotify"]
    assert status["available"] is False
    assert status["reason"] == "reconnect_required"
    assert not any("/me/player" in url for _, url, _ in server.calls)


def test_current_development_mode_profile_may_omit_product(announcement_app, client, clock):
    server = setup_spotify(announcement_app, clock)
    server.product = None
    connect(announcement_app, client)
    status = client.get("/api/announcements/spotify").json["spotify"]
    assert status["connected"] is True
    assert status["premium_status"] == "required_not_reported"


@pytest.mark.parametrize("after", ["disabled", "disconnected"])
def test_disable_or_disconnect_during_announcement_prevents_resume(
    announcement_app, client, clock, doubles, after
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    cast = doubles[1]
    detail = announce(announcement_app, client, clock, cast)
    path = "disconnect" if after == "disconnected" else "enable"
    assert (
        client.post(
            "/api/announcements/spotify/" + path,
            json={"enabled": False},
            headers=headers(announcement_app, client),
        ).status_code
        == 200
    )
    cast.report("192.168.1.119", detail["attempts"][0]["id"], "finished")
    announcement_app.announcement_service.tick()
    attempt = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json[
        "attempts"
    ][0]
    assert attempt["media_restore"] == "disabled"
    assert not any(url.endswith("/play") for _, url, _ in server.calls)


@pytest.mark.parametrize("member_changed", [False, True])
def test_group_restoration_uses_same_target_and_respects_member_volume(
    announcement_app, client, clock, doubles, member_changed
):
    from dataclasses import replace

    server = setup_spotify(announcement_app, clock)
    connect(announcement_app, client)
    service = announcement_app.announcement_service
    service.config = replace(service.config, targets=support.group_targets())
    server.player["device"]["id"] = "spotify-group"
    assert (
        client.post(
            "/api/announcements/spotify/enable",
            json={"enabled": True, "devices": {"jbl-group": "spotify-group"}},
            headers=headers(announcement_app, client),
        ).status_code
        == 200
    )
    wait_settings(client)
    cast = doubles[1]
    cast.speakers[support.GROUP].state = "playing"
    assert (
        client.post(
            "/api/announcements/schedules",
            json={
                "text": "Muista.",
                "target": "jbl-group",
                "due_local": "2027-01-15T14:01",
                "urgent": True,
            },
            headers=headers(announcement_app, client),
        ).status_code
        == 201
    )
    clock[0] += 60
    service.tick()
    detail = client.get("/api/announcements/occurrences").json["occurrences"][0]
    attempt_id = detail["attempts"][0]["id"]
    cast.report(support.GROUP, attempt_id, "playing")
    service.tick()
    if member_changed:
        cast.speakers["192.168.1.148"].volume = 0.65
    cast.report(support.GROUP, attempt_id, "finished")
    service.tick()
    detail = client.get("/api/announcements/occurrences/" + detail["occurrence"]["id"]).json
    assert detail["attempts"][0]["media_restore"] == (
        "skipped_newer_intent" if member_changed else "restored"
    )
    assert [play["target"] for play in cast.plays] == ["jbl-group"]
    assert detail["attempts"][0]["speaker_set"] == ["jbl-left", "jbl-right"]
    assert detail["occurrence"]["delivery"] == "delivered"


def test_slow_spotify_capture_cannot_start_speech_after_expiry(
    announcement_app, client, clock, doubles
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    server.during_pause = lambda: clock.__setitem__(0, clock[0] + 1000)
    detail = announce(announcement_app, client, clock, doubles[1])
    assert doubles[1].plays == []
    assert detail["attempts"][0]["reason"] == "expired_before_dispatch"


def test_owner_can_disable_restoration_even_when_refresh_access_is_expired(
    announcement_app, client, clock
):
    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    clock[0] += 3600
    server.refresh_error = True
    before = len(server.calls)
    response = client.post(
        "/api/announcements/spotify/enable",
        json={"enabled": False},
        headers=headers(announcement_app, client),
    )
    assert response.status_code == 200
    assert response.json["spotify"]["enabled"] is False
    assert len(server.calls) == before


def test_oauth_callback_records_connection_without_waiting_for_spotify(
    announcement_app, client, clock
):
    import threading
    from urllib.parse import parse_qs, urlsplit

    server = setup_spotify(announcement_app, clock)
    entered, release = threading.Event(), threading.Event()
    original = server.request

    def paused(method, url, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(method, url, **kwargs)

    announcement_app.announcement_service.spotify.http.request = paused
    response = client.post(
        "/api/announcements/spotify/connect", headers=headers(announcement_app, client)
    )
    state = parse_qs(urlsplit(response.json["authorization_url"]).query)["state"][0]
    done = threading.Event()
    responses = []

    def callback():
        responses.append(
            client.get(
                "/api/announcements/spotify/callback",
                query_string={"code": "code-secret", "state": state},
            )
        )
        done.set()

    worker = threading.Thread(target=callback)
    worker.start()
    assert entered.wait(2)
    try:
        assert done.wait(1), "OAuth callback waited for provider HTTP"
        assert responses[0].status_code == 302
        assert client.get("/api/announcements/spotify").json["spotify"]["operation"] == "connecting"
        # Disconnect supersedes an in-flight connection; its late reply must not reconnect.
        response = client.post(
            "/api/announcements/spotify/disconnect", headers=headers(announcement_app, client)
        )
        assert response.json["spotify"]["connected"] is False
    finally:
        release.set()
        worker.join(5)
    client.get("/api/announcements/spotify/devices")
    assert wait_settings(client)["connected"] is False


@pytest.mark.parametrize("operation", ["devices", "enable"])
def test_provider_settings_waits_do_not_block_speech_or_override_newer_disable(
    announcement_app, client, clock, doubles, operation
):
    import threading

    server = setup_spotify(announcement_app, clock)
    connect(announcement_app, client)
    service = announcement_app.announcement_service
    clock[0] += 3601  # Enabling and device discovery may both refresh tokens.
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    original = server.request

    def paused(method, url, **kwargs):
        if url.endswith("/api/token"):
            entered.set()
            assert release.wait(5)
        return original(method, url, **kwargs)

    service.spotify.http.request = paused
    responses = []

    def request():
        if operation == "devices":
            responses.append(client.get("/api/announcements/spotify/devices"))
        else:
            responses.append(
                client.post(
                    "/api/announcements/spotify/enable",
                    json={"enabled": True, "devices": {"jbl-right": "spotify-right"}},
                    headers=headers(announcement_app, client),
                )
            )
        returned.set()

    requester = threading.Thread(target=request)
    requester.start()
    assert entered.wait(2)
    try:
        assert returned.wait(1), "Settings handler waited for token refresh"
        assert responses[0].status_code in (200, 202)
        _, test = service.request_speaker_test(text="Still available", target="jbl-right")
        tick_done = threading.Event()
        tick = threading.Thread(target=lambda: (service.tick(), tick_done.set()))
        tick.start()
        assert tick_done.wait(1), "Settings provider wait blocked speech processing"
        service.request_stop(test["test"]["id"])
        service.tick()
        response = client.post(
            "/api/announcements/spotify/enable",
            json={"enabled": False},
            headers=headers(announcement_app, client),
        )
        assert response.json["spotify"]["enabled"] is False
    finally:
        release.set()
        requester.join(5)
    # A subsequent device read is a public barrier after the stale response.
    service.spotify.http.request = original
    client.get("/api/announcements/spotify/devices")
    status = wait_settings(client)
    assert status["enabled"] is False
    assert status["connected"] is True


def test_device_refresh_cannot_overwrite_newer_playback_credentials(
    announcement_app, client, clock
):
    import threading

    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    service = announcement_app.announcement_service
    clock[0] += 3601
    entered, release = threading.Event(), threading.Event()
    original = server.request
    token_number = [0]

    def rotating(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if url.endswith("/api/token"):
            token_number[0] += 1
            response.data["access_token"] = f"rotated-{token_number[0]}"
            response.data["refresh_token"] = f"refresh-{token_number[0]}"
        if url.endswith("/me/player/devices") and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return response

    service.spotify.http.request = rotating
    client.get("/api/announcements/spotify/devices")
    assert entered.wait(2)
    try:
        # Discovery publishes account refresh immediately, before device I/O.
        # Playback reuses it instead of issuing a second rotating refresh.
        assert service.spotify.media_state(service.config.target("jbl-right")) == "playing"
        assert token_number[0] == 1
    finally:
        release.set()
    wait_settings(client)
    client.get("/api/announcements/spotify/devices")
    wait_settings(client)
    device_calls = [kwargs for _, url, kwargs in server.calls if url.endswith("/me/player/devices")]
    assert device_calls[-1]["headers"]["Authorization"] == "Bearer rotated-1"
    assert client.get("/api/announcements/spotify").json["spotify"]["enabled"] is True


@pytest.mark.parametrize("first", ["settings", "playback"])
def test_concurrent_refresh_has_one_owner_without_blocking_speech(
    announcement_app, client, clock, first
):
    import threading

    server = setup_spotify(announcement_app, clock)
    enable(announcement_app, client)
    service = announcement_app.announcement_service
    clock[0] += 3601
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    original = server.request
    refreshes = []

    def paused(method, url, **kwargs):
        if url.endswith("/api/token"):
            refreshes.append(kwargs)
            entered.set()
            assert release.wait(5)
        return original(method, url, **kwargs)

    service.spotify.http.request = paused
    results = []

    def playback():
        results.append(service.spotify.media_state(service.config.target("jbl-right")))
        observed.set()

    player = threading.Thread(target=playback)
    if first == "settings":
        client.get("/api/announcements/spotify/devices")
    else:
        player.start()
    assert entered.wait(2)
    try:
        if first == "settings":
            player.start()
            assert observed.wait(1), "Playback waited for optional settings refresh"
            assert results == ["unknown"]
        else:
            client.get("/api/announcements/spotify/devices")
            assert wait_settings(client)["reason"] == "refresh_pending"
        assert len(refreshes) == 1
    finally:
        release.set()
        player.join(5)
    status = wait_settings(client)
    if first == "playback":
        assert status["reason"] == "refresh_pending"
    client.get("/api/announcements/spotify/devices")
    status = wait_settings(client)
    assert status["reason"] is None
    assert status["enabled"] is True
    assert len(refreshes) == 1
