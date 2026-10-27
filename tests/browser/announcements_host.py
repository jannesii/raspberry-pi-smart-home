"""Serve the announcement page with simulated synthesis/Cast/clock; never a real speaker."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
for key in ("DATABASE_URL", "USE_SQLA_READS"):
    os.environ.pop(key, None)

# Apply the repository's normal Eventlet ordering before Flask/stdlib networking.
import json

import pytest
from announcement_support import (
    GROUP,
    START,
    DiscordResponse,
    FakeCast,
    FakeDiscord,
    FakeSynthesizer,
    announcement_app,
    build_service,
    group_targets,
    login,
)
from flask import jsonify, request
from werkzeug.serving import make_server

import app  # noqa: F401

if __name__ == "__main__":
    work = Path(sys.argv[1])
    patch = pytest.MonkeyPatch()
    synth, cast = FakeSynthesizer(), FakeCast()
    clock = [START]
    discord = FakeDiscord()
    fixture = announcement_app.__wrapped__(work, patch, clock, (synth, cast), discord)
    flask_app = next(fixture)

    @flask_app.route("/test/cast/plays")
    def test_cast_plays():
        return jsonify([{"target": p["target"]} for p in cast.plays])

    @flask_app.route("/test/cast/report")
    def test_cast_report():
        latest = cast.plays[-1]
        host = {"jbl-right": "192.168.1.119", "jbl-left": "192.168.1.148", "jbl-group": GROUP}[
            latest["target"]
        ]
        cast.report(host, latest["attempt_id"], request.args["state"])
        return jsonify({"ok": True})

    @flask_app.route("/test/cast/speaker")
    def test_cast_speaker():
        cast.speakers[request.args["host"]].state = request.args["state"]
        return jsonify({"ok": True})

    @flask_app.route("/test/group")
    def test_group():
        # The owner has set the discovered group in ANNOUNCEMENT_TARGETS.
        flask_app.announcement_service = build_service(
            flask_app.ctrl,
            clock,
            (synth, cast),
            work / "cache",
            targets=group_targets(),
            discord=discord,
        )
        return jsonify({"ok": True})

    @flask_app.route("/test/synthesis")
    def test_synthesis():
        synth.failure = request.args.get("failure") or None
        return jsonify({"ok": True})

    @flask_app.route("/test/discord")
    def test_discord():
        outcome = request.args.get("outcome")
        if outcome == "rejected":
            discord.outcomes.append(DiscordResponse(503, {}))
        elif outcome == "unknown":
            import requests

            discord.outcomes.append(requests.Timeout("simulated acceptance unknown"))
        return jsonify({"requests": len(discord.requests)})

    @flask_app.route("/test/clock")
    def test_clock():
        from datetime import datetime

        clock[0] = datetime.fromisoformat(request.args["at"]).timestamp()
        return jsonify({"ok": True})

    @flask_app.route("/test/spotify")
    def test_spotify():
        from test_announcement_spotify import setup_spotify

        flask_app.test_spotify_server = setup_spotify(flask_app, clock)
        return jsonify({"ok": True})

    @flask_app.route("/test/spotify/owner")
    def test_spotify_owner():
        # A new track is independent owner intent at the external HTTP boundary.
        flask_app.test_spotify_server.player["item"]["uri"] = "spotify:track:owner-choice"
        return jsonify({"ok": True})

    @flask_app.route("/test/spotify/effects")
    def test_spotify_effects():
        calls = flask_app.test_spotify_server.calls
        return jsonify({"resumes": sum(url.endswith("/play") for _, url, _ in calls)})

    @flask_app.before_request
    def tick_on_request():
        # Stands in for the service loop: page polling drives processing, so the
        # flow is deterministic without a background thread.
        flask_app.announcement_service.tick()

    authenticated = login(flask_app.test_client())
    server = make_server("127.0.0.1", 0, flask_app, threaded=False)
    (work / "host.json").write_text(
        json.dumps(
            {
                "origin": f"http://127.0.0.1:{server.server_port}",
                "cookie": authenticated.get_cookie("session").value,
            }
        )
    )
    try:
        server.serve_forever()
    finally:
        fixture.close()
        patch.undo()
