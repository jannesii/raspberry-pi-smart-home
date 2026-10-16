"""ESP32-related API routes.

Endpoints:
- /esp32_temphum (GET, POST)
- /esp32_test (GET, POST)
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

import pytz
from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from ...extensions import csrf
from ...security import require_api_key
from ...services.weather import RENKO_ID, WeatherService

esp32_bp = Blueprint("api_esp32", __name__, url_prefix="")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if TYPE_CHECKING:
    from ...core import Controller
    from ...services.ac import ACThermostat


# In-memory state for ESP32 test telemetry (no DB persistence)
_esp32_test_last: dict[str, Any] = {
    "last_seen": None,  # UTC datetime
    "location": None,  # str | None
    "uptime_ms": None,  # int | None
    "remote_addr": None,  # str | None
    "data": None,  # original payload
}

_VALID_TEMPHUM_LOCATIONS = {
    "Keittiö",
    "Makuuhuone",
    "Tietokonepöytä",
    "WC",
    "Parveke",
    "Rengonharju",
    "Pelmaa",
    "test",
}
_TEMPERATURE_MIN_C = -50.0
_TEMPERATURE_MAX_C = 80.0
_HUMIDITY_MIN_PCT = 0.0
_HUMIDITY_MAX_PCT = 100.0
_SENSOR_SIDE_EFFECT_INTERVAL_S = 60.0
_sensor_side_effect_lock = threading.Lock()
_last_sensor_side_effect_started = 0.0


@esp32_bp.route("/esp32_temphum", methods=["GET"])
@login_required
def get_esp32_temphum():
    ctrl: Controller = current_app.ctrl  # type: ignore
    finland_tz = pytz.timezone("Europe/Helsinki")
    date_str = request.args.get("date", datetime.now(finland_tz).date().isoformat())
    location = request.args.get("location", "default").strip()
    logger.debug("API /esp32_temphum for %s by %s", date_str, current_user.get_id())
    data = ctrl.get_esp32_temphum_for_date(date_str, location)
    return jsonify(
        [
            {
                "timestamp": d.timestamp,
                "temperature": d.temperature,
                "humidity": d.humidity,
                "ac_on": getattr(d, "ac_on", None),
            }
            for d in data
        ]
    )


@esp32_bp.route("/esp32_temphum/latest", methods=["GET"])
@login_required
def get_latest_esp32_temphum():
    """Return the latest temperature/humidity reading for each location."""
    ctrl: Controller = current_app.ctrl  # type: ignore
    locations = ctrl.get_unique_locations()
    logger.debug(
        "API /esp32_temphum/latest returned %d locations for %s",
        len(locations),
        current_user.get_id(),
    )
    return jsonify(locations)


@esp32_bp.route("/esp32_temphum", methods=["POST"])
@require_api_key
@csrf.exempt
def post_esp32_temphum():
    data = request.get_json(silent=True) or {}
    payload, status_code = process_esp32_temphum_payload(data, source="http")
    return jsonify(payload), status_code


def process_esp32_temphum_payload(
    data: dict[str, Any], *, source: str = "http"
) -> tuple[dict[str, Any], int]:
    """Validate, persist, and broadcast one ESP32 temperature/humidity payload."""
    if not isinstance(data, dict):
        logger.warning("Invalid ESP32 temphum payload type source=%s", source)
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "JSON payload must be an object",
        }, 400

    logger.debug(
        "process_esp32_temphum_payload source=%s fields=%s",
        source,
        sorted(data),
    )
    location, temp, hum, error = (
        data.get("location"),
        data.get("temperature_c"),
        data.get("humidity_pct"),
        data.get("error"),
    )

    if error:
        logger.warning("ESP32 error source=%s location=%s error=%s", source, location, error)
        return {
            "ok": False,
            "error": "device_error",
            "message": str(error),
        }, 400

    if location is None or temp is None or hum is None:
        missing_fields = [
            field_name
            for field_name, value in (
                ("location", location),
                ("temperature_c", temp),
                ("humidity_pct", hum),
            )
            if value is None
        ]

        message = "Bad esp32 temphum payload; None fields: " + ", ".join(missing_fields)
        logger.warning("%s source=%s", message, source)
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": message,
        }, 400

    if not isinstance(location, str) or location not in _VALID_TEMPHUM_LOCATIONS:
        logger.warning("Invalid ESP32 location source=%s location=%r", source, location)
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "Invalid location",
        }, 400

    try:
        temp_value = float(temp)
        hum_value = float(hum)
    except (TypeError, ValueError):
        logger.warning(
            "Non-numeric ESP32 reading source=%s location=%s",
            source,
            location,
        )
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "Temperature and humidity must be numeric",
        }, 400

    if not math.isfinite(temp_value) or not math.isfinite(hum_value):
        logger.warning(
            "Non-finite ESP32 reading source=%s location=%s",
            source,
            location,
        )
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "Temperature and humidity must be finite",
        }, 400

    if not _TEMPERATURE_MIN_C <= temp_value <= _TEMPERATURE_MAX_C:
        logger.warning(
            "Out-of-range ESP32 temperature source=%s location=%s value=%s",
            source,
            location,
            temp_value,
        )
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "Temperature must be between -50 and 80 °C",
        }, 400

    if not _HUMIDITY_MIN_PCT <= hum_value <= _HUMIDITY_MAX_PCT:
        logger.warning(
            "Out-of-range ESP32 humidity source=%s location=%s value=%s",
            source,
            location,
            hum_value,
        )
        return {
            "ok": False,
            "error": "invalid_payload",
            "message": "Humidity must be between 0 and 100 percent",
        }, 400

    ctrl: Controller = current_app.ctrl
    # Derive current AC state if available
    ac_thermo: ACThermostat | None = None
    try:
        ac_thermo = getattr(current_app, "ac_thermostat", None)  # type: ignore
        ac_on_val: bool | None = bool(ac_thermo.is_on) if ac_thermo is not None else None
        if ac_on_val is None:
            # Fallback to persisted DB flag if thermostat not in memory
            conf = ctrl.get_thermostat_conf()
            if conf and conf.current_phase in ("on", "off"):
                ac_on_val = conf.current_phase == "on"
    except Exception:
        ac_on_val = None

    saved = ctrl.record_esp32_temphum(location, temp_value, hum_value, ac_on=ac_on_val)
    sio_handler = getattr(current_app, "sio_handler", None)
    if sio_handler is not None:
        sio_handler.emit_to_views(
            "esp32_temphum",
            {
                "location": saved.location,
                "temperature": saved.temperature,
                "humidity": saved.humidity,
                "ac_on": saved.ac_on,
            },
        )

    logger.debug(
        "Recorded ESP32 temphum: loc=%s temp=%.2f hum=%.2f",
        saved.location,
        saved.temperature,
        saved.humidity,
    )

    _schedule_sensor_side_effects(
        app=current_app._get_current_object(),
        ctrl=ctrl,
        sio_handler=sio_handler,
        ac_thermo=ac_thermo,
        weather_svc_pelmaa=getattr(current_app, "weather_service", None),
    )

    return {
        "ok": True,
        "received": {
            "location": saved.location,
            "temp": saved.temperature,
            "hum": saved.humidity,
        },
    }, 200


def _schedule_sensor_side_effects(
    *,
    app,
    ctrl: Controller,
    sio_handler,
    ac_thermo: ACThermostat | None,
    weather_svc_pelmaa: WeatherService | None,
) -> None:
    """Run thermostat and FMI refresh work off the telemetry request path."""
    global _last_sensor_side_effect_started

    now = monotonic()
    with _sensor_side_effect_lock:
        elapsed = now - _last_sensor_side_effect_started
        if elapsed < _SENSOR_SIDE_EFFECT_INTERVAL_S:
            logger.debug("Sensor side effects throttled elapsed_s=%.1f", elapsed)
            return
        _last_sensor_side_effect_started = now

    def _run() -> None:
        logger.debug("Running sensor side effects in background")
        with app.app_context():
            if ac_thermo is not None and getattr(ac_thermo, "_enabled", False):
                try:
                    ac_thermo.step_on_off_check()
                except Exception:
                    logger.exception("Background thermostat check failed")
            _fetch_and_log_outside_weather(
                ctrl=ctrl,
                sio_handler=sio_handler,
                weather_svc_pelmaa=weather_svc_pelmaa,
            )

    timer = threading.Timer(1.0, _run)
    timer.daemon = True
    timer.start()
    logger.debug("Scheduled sensor side effects")


def _fetch_and_log_outside_weather(
    *,
    ctrl: Controller,
    sio_handler,
    weather_svc_pelmaa: WeatherService | None,
) -> None:
    """Fetch FMI observations and persist them as outside sensor readings."""
    logger.debug("Fetching outside weather for sensor snapshots")
    if weather_svc_pelmaa is None:
        logger.debug("No Pelmaa weather service available")
        return

    try:
        weather_data_pelmaa = weather_svc_pelmaa.get_latest()
        weather_data_renko = WeatherService(fmisid=RENKO_ID).get_latest()
        observations = (
            ("Pelmaa", weather_data_pelmaa),
            ("Rengonharju", weather_data_renko),
        )

        for location, weather_data in observations:
            if weather_data.t2m is None or weather_data.t2m.value is None:
                logger.debug("No outside temperature available location=%s", location)
                continue

            humidity = weather_data.rh.value if weather_data.rh else 0.0
            saved = ctrl.record_esp32_temphum(
                location,
                float(weather_data.t2m.value),
                float(humidity if humidity is not None else 0.0),
                ac_on=None,
            )
            if sio_handler is not None:
                sio_handler.emit_to_views(
                    "esp32_temphum",
                    {
                        "location": saved.location,
                        "temperature": saved.temperature,
                        "humidity": saved.humidity,
                        "ac_on": None,
                    },
                )
            logger.debug(
                "Recorded outside weather location=%s temp=%.1fC hum=%.0f%%",
                saved.location,
                saved.temperature,
                saved.humidity,
            )
    except Exception:
        logger.exception("Failed to fetch/log outside weather")


@esp32_bp.route("/esp32_test", methods=["GET", "POST"])
@csrf.exempt
@require_api_key
def esp32_test():
    """
    Protected endpoint for testing ESP32 Wi-Fi/internet connectivity.

    - Requires API key (Authorization: Bearer <token> or X-API-Key).
    - POST: device sends JSON every second: { location, uptime_ms, ... } (stored in-memory only).
    - GET:  returns HTML page by default; `?format=json` returns latest status as JSON.
    """
    global _esp32_test_last
    logger.debug("esp32_test called method=%s", request.method)

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        # Normalize/validate minimal fields
        location = (
            (payload.get("location") or "default") if isinstance(payload, dict) else "default"
        )
        try:
            uptime_ms = (
                int(payload.get("uptime_ms"))
                if isinstance(payload, dict) and "uptime_ms" in payload
                else None
            )
        except Exception:
            uptime_ms = None
        remote = (
            (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
            .split(",")[0]
            .strip()
        )
        now_utc = datetime.now(UTC)

        _esp32_test_last = {
            "last_seen": now_utc,
            "location": location,
            "uptime_ms": uptime_ms,
            "remote_addr": remote,
            "data": payload,
        }
        logger.info("/api/esp32_test POST from %s loc=%s uptime_ms=%s", remote, location, uptime_ms)
        return jsonify(
            {
                "ok": True,
                "received": {"location": location, "uptime_ms": uptime_ms},
                "server_time": now_utc.isoformat(),
            }
        )

    # GET — return JSON if requested, otherwise render minimal standalone page
    def _status_json() -> dict[str, Any]:
        last = _esp32_test_last
        now_utc = datetime.now(UTC)
        last_seen = last.get("last_seen")
        age_s = None
        online = False
        if last_seen is not None:
            try:
                age_s = (now_utc - last_seen).total_seconds()
                online = age_s is not None and age_s <= 5.0
            except Exception:
                age_s = None
                online = False
        return {
            "online": bool(online),
            "age_seconds": age_s,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "location": last.get("location"),
            "uptime_ms": last.get("uptime_ms"),
            "remote_addr": last.get("remote_addr"),
            "data": last.get("data"),
        }

    wants_json = (
        request.args.get("format") == "json"
        or request.args.get("json") in {"1", "true", "yes"}
        or (request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"])
    )
    if wants_json:
        return jsonify(_status_json())
    return render_template("esp32_test.html")
