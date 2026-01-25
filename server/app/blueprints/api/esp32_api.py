"""ESP32-related API routes.

Endpoints:
- /esp32_temphum (GET, POST)
- /esp32_test (GET, POST)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict
from datetime import datetime, timezone

import pytz
from flask import Blueprint, request, jsonify, current_app, render_template
from flask_login import login_required, current_user

from ...core import Controller
from ...extensions import csrf
from ...security import require_api_key
from ...services.ac import ACThermostat


esp32_bp = Blueprint("api_esp32", __name__, url_prefix="")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# In-memory state for ESP32 test telemetry (no DB persistence)
_esp32_test_last: dict[str, Any] = {
    'last_seen': None,   # UTC datetime
    'location': None,    # str | None
    'uptime_ms': None,   # int | None
    'remote_addr': None,  # str | None
    'data': None,        # original payload
}


@esp32_bp.route('/esp32_temphum', methods=['GET'])
@login_required
def get_esp32_temphum():
    ctrl: Controller = current_app.ctrl  # type: ignore
    finland_tz = pytz.timezone('Europe/Helsinki')
    date_str = request.args.get(
        'date', datetime.now(finland_tz).date().isoformat())
    location = request.args.get('location', 'default').strip()
    logger.info(
        "API /esp32_temphum for %s by %s",
        date_str, current_user.get_id()
    )
    data = ctrl.get_esp32_temphum_for_date(date_str, location)
    return jsonify([
        {
            'timestamp': d.timestamp,
            'temperature': d.temperature,
            'humidity': d.humidity,
            'ac_on': getattr(d, 'ac_on', None)
        }
        for d in data
    ])


ac_check_flag = True


@esp32_bp.route('/esp32_temphum', methods=['POST'])
@require_api_key
@csrf.exempt
def post_esp32_temphum():

    data = request.get_json(silent=True) or {}
    location, temp, hum, error = data.get('location'), data.get(
        'temperature_c'), data.get('humidity_pct'), data.get('error')

    if error:
        logger.warning(f'ESP32 ERROR: {error} | Location: {location}')
        return jsonify({
            'ok': False,
            'error': 'device_error',
            'message': str(error),
        }), 400

    if location is None or temp is None or hum is None:
        logger.warning("Bad esp32 temphum payload: %s", data)
        return jsonify({
            'ok': False,
            'error': 'invalid_payload',
            'message': 'location, temperature_c, humidity_pct required',
        }), 400

    valid_locations = ["Keittiö", "Makuuhuone",
                       "Tietokonepöytä", "WC", "Parveke", "Outside", "test"]
    if location not in valid_locations:
        logger.warning(f"Invalid esp32 location: {location}")
        return jsonify({
            'ok': False,
            'error': 'invalid_payload',
            'message': 'Invalid location',
        }), 400

    ctrl: Controller = current_app.ctrl
    # Derive current AC state if available
    try:
        ac_thermo: ACThermostat | None = getattr(
            current_app, 'ac_thermostat', None)  # type: ignore
        ac_on_val: bool | None = bool(
            ac_thermo.is_on) if ac_thermo is not None else None
        if ac_on_val is None:
            # Fallback to persisted DB flag if thermostat not in memory
            conf = ctrl.get_thermostat_conf()
            if conf and conf.current_phase in ('on', 'off'):
                ac_on_val = (conf.current_phase == 'on')
    except Exception:
        ac_on_val = None

    saved = ctrl.record_esp32_temphum(
        location, temp, hum, ac_on=ac_on_val)
    current_app.sio_handler.emit_to_views('esp32_temphum', {
        'location': saved.location,
        'temperature': saved.temperature,
        'humidity':    saved.humidity,
        'ac_on':       saved.ac_on
    })

    logger.debug("Recorded ESP32 temphum: loc=%s temp=%.2f hum=%.2f",
                 saved.location, saved.temperature, saved.humidity)

    def reset_flag():
        global ac_check_flag
        ac_check_flag = True

    def fetch_and_log_outside_weather():
        """Fetch FMI weather data and log as 'Outside' sensor reading."""
        try:
            from ...services.weather import WeatherService
            weather_svc: WeatherService | None = getattr(
                current_app, 'weather_service', None)
            if weather_svc is None:
                logger.debug(
                    "No weather_service available for Outside reading")
                return

            weather_data = weather_svc.get_latest()
            logger.debug("FMI weather data: t2m=%s, rh=%s",
                         weather_data.t2m, weather_data.rh)

            if weather_data.t2m is None or weather_data.t2m.value is None:
                logger.debug("No t2m value from FMI, skipping Outside reading")
                return

            outside_temp = weather_data.t2m.value
            outside_hum = weather_data.rh.value if weather_data.rh else None

            # Use 0.0 humidity if not available (FMI sometimes doesn't report rh)
            if outside_hum is None:
                outside_hum = 0.0

            # Record to DB as "Outside" location
            outside_saved = ctrl.record_esp32_temphum(
                "Outside", outside_temp, outside_hum, ac_on=None)

            # Broadcast to views
            current_app.sio_handler.emit_to_views('esp32_temphum', {
                'location': outside_saved.location,
                'temperature': outside_saved.temperature,
                'humidity': outside_saved.humidity,
                'ac_on': None
            })

            logger.debug("Recorded Outside weather: temp=%.1f°C hum=%.0f%%",
                        outside_saved.temperature, outside_saved.humidity)
        except Exception as e:
            logger.exception("Failed to fetch/log Outside weather: %s", e)

    # Trigger immediate thermostat check once per minute at most
    global ac_check_flag
    if ac_check_flag:
        ac_check_flag = False
        # Wait a second to make sure all sensors have reported
        threading.Timer(1, ac_thermo.step_on_off_check).start(
        ) if ac_thermo._enabled else None
        # Also fetch and log outside weather from FMI
        fetch_and_log_outside_weather()
        # Reset flag after 10 seconds
        threading.Timer(10, reset_flag).start()

    return jsonify({
        'ok': True,
        'received': {
            'location': saved.location,
            'temp': saved.temperature,
            'hum': saved.humidity
        },
    })


@esp32_bp.route('/esp32_test', methods=['GET', 'POST'])
@csrf.exempt
def esp32_test():
    """
    Protected endpoint for testing ESP32 Wi‑Fi/internet connectivity.

    - Requires API key (Authorization: Bearer <token> or X-API-Key).
    - POST: device sends JSON every second: { location, uptime_ms, ... } (stored in-memory only).
    - GET:  returns HTML page by default; `?format=json` returns latest status as JSON.
    """
    global _esp32_test_last

    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        # Normalize/validate minimal fields
        location = (payload.get('location') or 'default') if isinstance(
            payload, dict) else 'default'
        try:
            uptime_ms = int(payload.get('uptime_ms')) if isinstance(
                payload, dict) and 'uptime_ms' in payload else None
        except Exception:
            uptime_ms = None
        remote = (request.headers.get('X-Forwarded-For')
                  or request.remote_addr or '').split(',')[0].strip()
        now_utc = datetime.now(timezone.utc)

        _esp32_test_last = {
            'last_seen': now_utc,
            'location': location,
            'uptime_ms': uptime_ms,
            'remote_addr': remote,
            'data': payload,
        }
        logger.info("/api/esp32_test POST from %s loc=%s uptime_ms=%s",
                    remote, location, uptime_ms)
        return jsonify({
            'ok': True,
            'received': {'location': location, 'uptime_ms': uptime_ms},
            'server_time': now_utc.isoformat(),
        })

    # GET — return JSON if requested, otherwise render minimal standalone page
    def _status_json() -> Dict[str, Any]:
        last = _esp32_test_last
        now_utc = datetime.now(timezone.utc)
        last_seen = last.get('last_seen')
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
            'online': bool(online),
            'age_seconds': age_s,
            'last_seen': last_seen.isoformat() if last_seen else None,
            'location': last.get('location'),
            'uptime_ms': last.get('uptime_ms'),
            'remote_addr': last.get('remote_addr'),
            'data': last.get('data'),
        }

    wants_json = (
        request.args.get('format') == 'json' or
        request.args.get('json') in {'1', 'true', 'yes'} or
        (request.accept_mimetypes['application/json']
         >= request.accept_mimetypes['text/html'])
    )
    if wants_json:
        return jsonify(_status_json())
    return render_template('esp32_test.html')
