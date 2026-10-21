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
import uuid

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

        from datetime import UTC, datetime

        from flask import request
        from test_car_heater_sensors import _DummySIOHandler, _DummySocketIO

        from app.blueprints.api.car_heater import status as car_status
        from app.services.esp32_redis_bridge import ESP32RedisBridge

        flask_app.sio_handler = _DummySIOHandler()

        @flask_app.post("/test/frame")
        def device_frame():
            """One firmware-shaped frame through the real HTTP or WebSocket ingest."""
            spec = request.get_json()
            run = heater.get_bounded_session()
            session_id = {"current": run and run["session_id"], "old": "old-session"}
            now = datetime.now(UTC)
            frame = {
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "firmware_version": "3.1.1",
                "shelly_connected": spec.get("connected", True),
                "temperature": None,
                "sensors": {
                    "glass_reference": {"status": "missing", "temperature_c": None},
                    "cabin": {
                        "status": "ok",
                        "temperature_c": -2.5,
                        "humidity_pct": 0,
                        "age_ms": 0,
                    },
                },
            }
            if spec.get("connected", True):
                relay = spec.get("relay", False)
                frame["shelly"] = json.dumps(
                    {"output": relay, "apower": spec.get("power", 0), "aenergy": {"total": 1}}
                )
                frame["bounded"] = {
                    "protocol": 1,
                    "ready": True,
                    "relay_on": relay,
                    "power_w": spec.get("power", 0),
                    "boot_id": "relay-one",
                    "device_boot_id": "esp-one",
                    **(
                        {"session_id": session_id[spec["session"]], "phase": spec.get("phase")}
                        if spec.get("session")
                        else {}
                    ),
                }
            if spec.get("ack"):
                ack = spec["ack"]
                frame["action_results"] = [
                    {
                        "action": ack["action"],
                        "success": ack["success"],
                        "session_id": session_id[ack["session"]],
                    }
                ]
            if spec.get("transport") == "ws":
                frame["received_at"] = now.isoformat()
                ESP32RedisBridge(
                    app=flask_app, socketio=_DummySocketIO(), sio_handler=flask_app.sio_handler
                )._emit_status(frame)
            else:
                _, code = car_status.handle_status_update_request(frame, commands_enabled=True)
                assert code == 200
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

    if len(sys.argv) > 2 and sys.argv[2] == "review":
        import hashlib
        from datetime import UTC, datetime, timedelta

        import test_car_heater_session_review as review

        from app.blueprints.api.car_heater import control as car_control, status as car_status
        from app.core.models import CarHeaterPhoto

        flask_app.sio_handler = review._DummySIOHandler()
        patch.setattr(car_control, "process_commands", lambda data, car: ([], None, None))
        patch.setattr(car_control, "sync_runtime_command_state", lambda data, car: (None, None))
        patch.setattr(car_status, "_utc_now", lambda: review._NOW[0])

        @flask_app.post("/test/review-session")
        def review_session():
            """A 05:00 (Helsinki) manual session with simulated frames and two photos."""
            ctrl = flask_app.ctrl
            attempt = ctrl.create_car_heater_session(
                trigger="manual",
                observed_at="2026-10-02T02:00:00+00:00",
                requested_departure_at=None,
                heating_duration_minutes=60,
                snapshot={"installation": "Reference lower-middle", "software_version": "1.0.0"},
            )
            start = datetime(2026, 10, 2, 2, tzinfo=UTC)
            for i in range(121):
                if 70 <= i < 74:
                    continue  # two-minute reporting outage
                reference = round(-6 + i * 0.1, 2)
                review.post_frame(
                    flask_app,
                    (start + timedelta(seconds=30 * i)).isoformat(),
                    reference=review.ok(reference),
                    comparison={"status": "missing", "temperature_c": None, "age_ms": None}
                    if 40 <= i < 46
                    else review.ok(round(reference - 1.5, 2)),
                    cabin=review.ok(round(-2 + i * 0.15, 2), humidity_pct=round(85 - i * 0.2, 2)),
                    shelly=review.shelly(1650, 1000 + i * 0.4) if i < 60 else None,
                )
            content = b"\xff\xd8\xff\xe0" + b"windshield" * 32 + b"\xff\xd9"
            for minute in (50, 60):
                captured = (start + timedelta(minutes=minute)).isoformat()
                ctrl.save_car_heater_photo(
                    CarHeaterPhoto(
                        id=str(uuid.uuid4()),
                        session_id=attempt.id,
                        captured_at=captured,
                        due_at=None,
                        capture_kind="manual",
                        sequence=None,
                        observation_started_at=None,
                        camera_label=None,
                        received_at=captured,
                        content_type="image/jpeg",
                        byte_size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                    ),
                    content,
                    max_per_session=100,
                )
            return {"session_id": attempt.id}

    if len(sys.argv) > 2 and sys.argv[2] == "weather":
        from datetime import timedelta

        import requests
        import test_weather_observation_archive as weather

        from app.services.weather.observation_archive import ObservationCollector

        fmi = weather.FakeFMI()
        clock = [weather.START.timestamp()]
        fmi.replies[weather.PELMAA] = weather.fmi_reply(
            weather.PELMAA,
            {
                "t2m": weather.every_ten(weather.START, ["-6.0", "-6.2"]),
                "n_man": weather.every_ten(weather.START, ["NaN", "NaN"]),
            },
        )
        fmi.replies[weather.AIRPORT] = requests.Timeout("slow")
        collector = ObservationCollector(flask_app.ctrl, http=fmi, clock=lambda: clock[0])
        flask_app.weather_collector = collector
        collector.run_pending()
        # The fixed clock keeps the running thread from collecting again on its own.
        collector.start()

        import test_weather_forecast_archive as forecast

        from app.services.weather.forecast_archive import ForecastCollector

        forecast_fmi = forecast.FakeForecastFMI()
        series = {
            p: forecast.hourly(forecast.ORIGIN, ["1.0", "2.0", "3.0"]) for p in forecast.FIELDS
        }
        series["RadiationLW"] = forecast.hourly(forecast.ORIGIN, ["NaN", "NaN", "NaN"])
        del series["WindGust"]
        forecast_fmi.reply = forecast.forecast_reply(series)
        forecaster = ForecastCollector(flask_app.ctrl, http=forecast_fmi, clock=lambda: clock[0])
        flask_app.weather_forecast_collector = forecaster
        forecaster.run_pending()
        forecaster.start()

        @flask_app.post("/test/weather-recover")
        def weather_recover():
            clock[0] += timedelta(minutes=10).total_seconds()
            fmi.replies[weather.AIRPORT] = weather.fmi_reply(
                weather.AIRPORT, {"vis": weather.every_ten(weather.START, ["5590.0"])}
            )
            collector.run_pending()
            return {"ok": True}

    if len(sys.argv) > 2 and sys.argv[2] == "weather_evidence":
        from datetime import timedelta

        import requests
        import test_weather_forecast_archive as forecast
        import test_weather_observation_recovery as recovery
        from test_weather_evidence_export import run_with

        from app.services.weather.forecast_archive import ForecastCollector
        from app.services.weather.observation_archive import ObservationCollector

        # The real collection path with #26's initialization backfill; one
        # station times out so its gap stays visible and repairable.
        station_fmi = recovery.StationFMI()
        station_fmi.failing[recovery.AIRPORT] = requests.Timeout("slow")
        clock = [recovery.START.timestamp()]
        collector = ObservationCollector(flask_app.ctrl, http=station_fmi, clock=lambda: clock[0])
        flask_app.weather_collector = collector
        collector.run_pending()
        forecast_fmi = forecast.FakeForecastFMI()
        forecast_fmi.reply = run_with(["0.0", "NaN", "-1.5", "-2.0", "-2.5", "-3.0"])
        forecaster = ForecastCollector(flask_app.ctrl, http=forecast_fmi, clock=lambda: clock[0])
        flask_app.weather_forecast_collector = forecaster
        forecaster.run_pending()

        @flask_app.post("/test/weather-session")
        def weather_session():
            """A manual session five hours before the first collection, plus one now."""
            ids = []
            for hours in (5, 0):
                attempt = flask_app.ctrl.create_car_heater_session(
                    trigger="manual",
                    observed_at=(recovery.START - timedelta(hours=hours)).isoformat(),
                    requested_departure_at=None,
                    heating_duration_minutes=60,
                    snapshot={"software_version": "1.0.0"},
                )
                ids.append(attempt.id)
            return {"backfilled": ids[0], "current": ids[1]}

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
