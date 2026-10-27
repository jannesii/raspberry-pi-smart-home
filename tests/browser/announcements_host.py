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
from announcement_support import START, FakeCast, FakeSynthesizer, announcement_app, login
from flask import jsonify, request
from werkzeug.serving import make_server

import app  # noqa: F401

if __name__ == "__main__":
    work = Path(sys.argv[1])
    patch = pytest.MonkeyPatch()
    synth, cast = FakeSynthesizer(), FakeCast()
    clock = [START]
    fixture = announcement_app.__wrapped__(work, patch, clock, (synth, cast))
    flask_app = next(fixture)
    service = flask_app.announcement_service

    @flask_app.route("/test/cast/plays")
    def test_cast_plays():
        return jsonify([{"target": p["target"]} for p in cast.plays])

    @flask_app.route("/test/cast/report")
    def test_cast_report():
        latest = cast.plays[-1]
        host = {"jbl-right": "192.168.1.119", "jbl-left": "192.168.1.148"}[latest["target"]]
        cast.report(host, latest["attempt_id"], request.args["state"])
        return jsonify({"ok": True})

    @flask_app.route("/test/clock")
    def test_clock():
        from datetime import datetime

        clock[0] = datetime.fromisoformat(request.args["at"]).timestamp()
        return jsonify({"ok": True})

    @flask_app.before_request
    def tick_on_request():
        # Stands in for the service loop: page polling drives processing, so the
        # flow is deterministic without a background thread.
        service.tick()

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
