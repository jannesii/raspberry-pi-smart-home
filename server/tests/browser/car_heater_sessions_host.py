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
    if len(sys.argv) > 2 and sys.argv[2] == "bounded":
        from app.services.car_heater.car_heater_service import CarHeaterService

        patch.setattr("redis.from_url", lambda *a, **kw: (_ for _ in ()).throw(OSError("isolated")))
        heater = CarHeaterService(flask_app.ctrl)
        flask_app.car_heater_service = heater
        heater.observe_bounded_status(
            {
                "bounded": {
                    "protocol": 1,
                    "ready": True,
                    "relay_on": False,
                    "boot_id": "relay-one",
                    "device_boot_id": "esp-one",
                }
            }
        )

        @flask_app.post("/test/relay-off")
        def relay_off():
            run = heater.get_bounded_session()
            heater.observe_bounded_status(
                {
                    "bounded": {
                        "protocol": 1,
                        "ready": True,
                        "relay_on": False,
                        "session_id": run["session_id"],
                        "phase": "stopped",
                        "reason": "cancelled",
                        "boot_id": "relay-one",
                        "device_boot_id": "esp-one",
                    }
                }
            )
            return {"ok": True}

    if len(sys.argv) > 2 and sys.argv[2] == "departure":
        from datetime import UTC, datetime

        from flask import request
        from test_departure_heating import departure

        departure_fixture = departure.__wrapped__(flask_app, patch)
        planner, heater, clock, observation = next(departure_fixture)
        clock[0] = datetime(2027, 1, 15, 22, 30, tzinfo=UTC).timestamp()
        observation.time = datetime.fromtimestamp(clock[0], UTC).isoformat()

        @flask_app.post("/test/departure-time")
        def departure_time():
            plan = planner.get_plan()
            clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp() + 600
            observation.time = datetime.fromtimestamp(clock[0], UTC).isoformat()
            heater.observe_bounded_status(
                {
                    "bounded": {
                        "protocol": 1,
                        "ready": True,
                        "relay_on": False,
                        "power_w": 0,
                        "boot_id": "relay",
                        "device_boot_id": "esp",
                    }
                }
            )
            heater.tick_departure()
            return {"now": clock[0]}

        @flask_app.post("/test/departure-weather")
        def departure_weather():
            observation.value = request.get_json()["temperature"]
            return {"ok": True}

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
