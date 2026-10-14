"""Serve only isolated test routes/Controller; never initialize live integrations."""

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
from test_car_heater_sessions import client, session_app
from werkzeug.serving import make_server

import app  # noqa: F401

if __name__ == "__main__":
    work = Path(sys.argv[1])
    patch = pytest.MonkeyPatch()
    fixture = session_app.__wrapped__(work, patch)
    flask_app = next(fixture)
    authenticated = client.__wrapped__(flask_app)
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
