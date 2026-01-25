"""Car Heater Status module - ESP32 status updates and alerts.

Handles:
- Status POST endpoints (normal and test mode)
- Shelly payload parsing
- Alert processing (stuck heater, no power, missing telemetry)
- KFactor/ReadyBy tick integration
- Socket.IO status broadcasting
"""

from datetime import datetime, timezone
from dataclasses import asdict
from typing import Any, Dict, List, Tuple, TYPE_CHECKING
import logging
import json
import time
import os
from zoneinfo import ZoneInfo

from flask import request, jsonify, current_app

from ....core import Controller
from ....core.models import CarHeaterStatus
from ....extensions import csrf
from ....security import require_api_key
from ._blueprint import car_bp

if TYPE_CHECKING:
    from ....sockets import SocketEventHandler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ==============================================================================
# Alert Configuration
# ==============================================================================

_ALERT_MIN_POWER_W = float(os.getenv("CAR_HEATER_ALERT_MIN_POWER_W", "5"))
_ALERT_NO_POWER_MINUTES = int(
    os.getenv("CAR_HEATER_ALERT_NO_POWER_MINUTES", "10"))
_ALERT_STUCK_ON_MINUTES = int(
    os.getenv("CAR_HEATER_ALERT_STUCK_ON_MINUTES", "180"))
_ALERT_SHELLY_MISSING_MINUTES = int(
    os.getenv("CAR_HEATER_ALERT_SHELLY_MISSING_MINUTES", "15"))


class _CarHeaterAlertState:
    """Track state for alert thresholds."""

    def __init__(self) -> None:
        self.low_power_since: datetime | None = None
        self.heater_on_since: datetime | None = None
        self.last_shelly_ts: datetime | None = None
        self.last_heater_on: bool | None = None


_ALERT_STATE = _CarHeaterAlertState()


class FallbackStatus:
    """Global fallback status when Shelly data is unavailable."""

    def __init__(self) -> None:
        self.timestamp: datetime | None = None
        self.ambient_temp: float | None = None
        self.shelly_connected: bool | None = None


# Global fallback status instance
fallback_status = FallbackStatus()

# Test mode state
TEST_MODE_TIMEOUT_SECONDS = 10.0
_last_test_request_time: float | None = None
_test_mode_was_active: bool = False


def _is_test_mode_active() -> bool:
    """Check if test mode is active (test request received within timeout)."""
    if _last_test_request_time is None:
        return False
    return (time.monotonic() - _last_test_request_time) < TEST_MODE_TIMEOUT_SECONDS


# ==============================================================================
# Parsing Utilities
# ==============================================================================

def parse_timestamp(raw_ts: str) -> datetime:
    """Parse timestamp string (UTC) and convert to Helsinki time."""
    dt_utc = datetime.strptime(
        raw_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(ZoneInfo("Europe/Helsinki"))


def parse_shelly_payload(shelly_raw: str | None) -> Dict[str, Any]:
    """Parse raw Shelly JSON string into a dict."""
    if not shelly_raw:
        return {}
    try:
        return json.loads(shelly_raw)
    except json.JSONDecodeError:
        logger.warning("Failed to decode shelly JSON: %r", shelly_raw)
        return {}


def build_car_heater_status(
    timestamp: datetime,
    shelly: Dict[str, Any],
    ambient_temp: float | None,
) -> CarHeaterStatus:
    """Build CarHeaterStatus from Shelly data."""
    aenergy = shelly.get("aenergy") or {}
    shelly_temp = shelly.get("temperature") or {}

    return CarHeaterStatus(
        id=None,
        timestamp=timestamp,
        is_heater_on=bool(shelly.get("output")),
        instant_power_w=shelly.get("apower", 0.0),
        voltage_v=shelly.get("voltage"),
        current_a=shelly.get("current"),
        energy_total_wh=aenergy.get("total"),
        energy_last_min_wh=(aenergy.get("by_minute") or [None])[0],
        energy_ts=aenergy.get("minute_ts"),
        device_temp_c=shelly_temp.get("tC"),
        device_temp_f=shelly_temp.get("tF"),
        ambient_temp=ambient_temp,
        source=shelly.get("source"),
    )


# ==============================================================================
# Payload Building
# ==============================================================================

def record_and_build_payload(
    ctrl: Controller,
    car: CarHeaterStatus,
    skip_db: bool = False,
) -> Dict[str, Any]:
    """Persist status to DB and return the status payload dict."""
    recorded_id = None
    if not skip_db:
        try:
            recorded = ctrl.record_car_heater_status(car)
            recorded_id = getattr(recorded, "id", None)
        except Exception as e:
            logger.exception("Failed to record car heater status: %s", e)

    return {
        "id": recorded_id,
        "timestamp": car.timestamp.isoformat() if hasattr(car.timestamp, "isoformat") else car.timestamp,
        "is_heater_on": car.is_heater_on,
        "instant_power_w": car.instant_power_w,
        "voltage_v": car.voltage_v,
        "current_a": car.current_a,
        "energy_total_wh": car.energy_total_wh,
        "energy_last_min_wh": car.energy_last_min_wh,
        "energy_ts": car.energy_ts,
        "device_temp_c": car.device_temp_c,
        "device_temp_f": car.device_temp_f,
        "ambient_temp": car.ambient_temp,
        "source": car.source,
    }


def build_fallback_payload(
    timestamp: datetime,
    ambient_temp: float | None,
    shelly_connected: bool,
) -> Dict[str, Any]:
    """Build payload when no Shelly data is available."""
    return {
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
        "ambient_temp": ambient_temp,
        "shelly_connected": shelly_connected,
    }


# ==============================================================================
# Alert Processing
# ==============================================================================

def _process_alerts(
    *,
    car: CarHeaterStatus | None,
    shelly_connected: bool,
    timestamp: datetime,
    is_test: bool,
) -> None:
    """Process alert conditions for car heater status."""
    if is_test:
        return

    from ....services.alert_webhook import record_alert

    if car is not None:
        _ALERT_STATE.last_shelly_ts = timestamp
        _ALERT_STATE.last_heater_on = bool(car.is_heater_on)

        if car.is_heater_on:
            if _ALERT_STATE.heater_on_since is None:
                _ALERT_STATE.heater_on_since = timestamp
            duration_on = (
                timestamp - _ALERT_STATE.heater_on_since).total_seconds()
            if duration_on >= _ALERT_STUCK_ON_MINUTES * 60:
                record_alert(
                    key="heater_stuck_on",
                    title="Car heater stuck ON",
                    message=f"heater_on_duration_min={duration_on/60:.1f}",
                )

            power_w = float(car.instant_power_w or 0.0)
            if power_w < _ALERT_MIN_POWER_W:
                if _ALERT_STATE.low_power_since is None:
                    _ALERT_STATE.low_power_since = timestamp
                low_power_s = (
                    timestamp - _ALERT_STATE.low_power_since).total_seconds()
                if low_power_s >= _ALERT_NO_POWER_MINUTES * 60:
                    record_alert(
                        key="heater_on_no_power",
                        title="Heater ON but no power draw",
                        message=f"power_w={power_w:.1f} duration_min={low_power_s/60:.1f}",
                    )
            else:
                _ALERT_STATE.low_power_since = None
        else:
            _ALERT_STATE.heater_on_since = None
            _ALERT_STATE.low_power_since = None

    if not shelly_connected:
        last_ts = _ALERT_STATE.last_shelly_ts
        if last_ts is not None:
            missing_s = (timestamp - last_ts).total_seconds()
            if missing_s >= _ALERT_SHELLY_MISSING_MINUTES * 60:
                last_heater = _ALERT_STATE.last_heater_on
                record_alert(
                    key="shelly_missing",
                    title="Shelly telemetry missing",
                    message=f"missing_min={missing_s/60:.1f} last_heater_on={last_heater}",
                )


# ==============================================================================
# Service Ticks
# ==============================================================================

def run_keep_at_temp_tick(car: CarHeaterStatus) -> None:
    """Run the keep-at-temperature thermostat tick."""
    try:
        from ....services.car_heater import KeepAtTempService
        svc: KeepAtTempService | None = getattr(
            current_app, "keep_at_temp_service", None)
        if svc:
            svc.tick(current_temp=car.ambient_temp, heater_on=car.is_heater_on)
    except Exception as e:
        logger.exception("Failed to run keep-at-temp tick: %s", e)


def run_kfactor_tick(car: CarHeaterStatus, ambient_temp: float | None) -> None:
    """Run the KFactor calibration tick."""
    try:
        from ....services.car_heater import KFactorCalibrator
        ksvc: KFactorCalibrator | None = getattr(
            current_app, "kfactor_calibrator", None)
        if ksvc is not None:
            ksvc.tick(
                is_heater_on=bool(car.is_heater_on),
                power_w=float(car.instant_power_w or 0),
                cabin_temp_c=float(ambient_temp or 0),
            )
    except Exception as e:
        logger.exception("Failed to run kfactor tick: %s", e)


def run_ready_by_tick(
    car: CarHeaterStatus,
    outside_temp: float | None,
    is_test: bool,
    sio: "SocketEventHandler",
) -> None:
    """Run the Ready-by scheduler tick."""
    try:
        from ....services.car_heater import ReadyByService
        rsvc: ReadyByService | None = getattr(
            current_app, "ready_by_service", None)
        if rsvc is not None:
            rsvc.tick(car, outside_temp_c=outside_temp, is_test=is_test)
            try:
                sio.emit_ready_by_status_to_views()
            except Exception:
                pass
    except Exception as e:
        logger.exception("Failed to run ready-by tick: %s", e)


# ==============================================================================
# Main Status Handler
# ==============================================================================

def handle_status_update_request(
    data: Dict[str, Any] | None,
    commands_enabled: bool = True,
    is_test: bool = False,
) -> Tuple[Any, int]:
    """
    Shared handler for car heater status update requests.

    Args:
        data: JSON payload from request
        commands_enabled: Whether to process commands

    Returns:
        Flask response tuple (jsonify response, status code)
    """
    from .control import process_commands

    start_time = time.perf_counter()

    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

    sio: "SocketEventHandler" = getattr(current_app, "sio_handler", None)
    if sio is None:
        return jsonify({"error": "SocketEventHandler not initialized"}), 500

    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Parse incoming data
    timestamp = parse_timestamp(data.get("timestamp"))
    shelly = parse_shelly_payload(data.get("shelly"))
    shelly_connected = bool(data.get("shelly_connected", True))
    ambient_temp = data.get("temperature")

    # Update global fallback status
    fallback_status.timestamp = timestamp
    fallback_status.ambient_temp = ambient_temp
    fallback_status.shelly_connected = shelly_connected

    # Build status and payload
    car: CarHeaterStatus | None = None
    status_payload: Dict[str, Any]

    if shelly and shelly_connected:
        car = build_car_heater_status(timestamp, shelly, ambient_temp)
        status_payload = record_and_build_payload(ctrl, car, skip_db=is_test)
        _process_alerts(
            car=car,
            shelly_connected=shelly_connected,
            timestamp=timestamp,
            is_test=is_test,
        )
        run_keep_at_temp_tick(car)
        run_kfactor_tick(car, ambient_temp)
        run_ready_by_tick(car, data.get("outside_temp"), is_test, sio)

        # Broadcast kFactor status to all connected views
        try:
            from ....services.car_heater import KFactorCalibrator
            ksvc: KFactorCalibrator | None = getattr(
                current_app, "kfactor_calibrator", None)
            if ksvc is not None:
                sio.emit_kfactor_status(ksvc)
        except Exception:
            pass
    else:
        logger.debug("No shelly data provided in car heater status update")
        status_payload = build_fallback_payload(
            timestamp, ambient_temp, shelly_connected)
        _process_alerts(
            car=None,
            shelly_connected=shelly_connected,
            timestamp=timestamp,
            is_test=is_test,
        )

    # Process commands
    commands: List[Dict[str, Any]] = []
    command_status: Dict[str, Any] | None = None
    charge_mode_state: Dict[str, Any] | None = None

    if commands_enabled:
        commands, command_status, charge_mode_state = process_commands(
            data, car)
    else:
        logger.debug(
            "Car heater commands are disabled; skipping command queue handling.")

    # Notify browser clients
    sio.emit_car_heater_status_to_views(
        status_payload, command_status, charge_mode_state)

    elapsed_time = time.perf_counter() - start_time
    logger.debug(
        "Processed car heater status update in %.3f seconds", elapsed_time)

    return jsonify(commands), 200


# ==============================================================================
# Routes
# ==============================================================================

@car_bp.route('/status', methods=['POST'])
@csrf.exempt
@require_api_key
def update_car_heater_status():
    """Update the car heater status and return queued commands."""
    global _test_mode_was_active

    if _is_test_mode_active():
        logger.debug("Normal endpoint blocked - test mode active")
        return jsonify([]), 200  # Return empty commands, don't process

    # Log when test mode expires
    if _test_mode_was_active:
        logger.info("Test mode expired - normal endpoint re-enabled")
        _test_mode_was_active = False

    return handle_status_update_request(
        data=request.get_json(),
        commands_enabled=True,
    )


@car_bp.route('/status/test', methods=['POST'])
@csrf.exempt
@require_api_key
def update_car_heater_status_test():
    """Test endpoint for car heater status (requires API key)."""
    global _last_test_request_time, _test_mode_was_active

    # Log if test mode is being activated (not already active)
    if not _is_test_mode_active():
        logger.info(
            "Test mode activated - normal endpoint disabled for %.0f seconds",
            TEST_MODE_TIMEOUT_SECONDS,
        )
    _last_test_request_time = time.monotonic()
    _test_mode_was_active = True

    return handle_status_update_request(
        data=request.get_json(),
        commands_enabled=True,
        is_test=True,
    )
