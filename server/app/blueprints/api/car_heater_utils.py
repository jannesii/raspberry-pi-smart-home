"""Car Heater API utility functions."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, TYPE_CHECKING
from dataclasses import asdict
import logging
import json
import time
import os
from zoneinfo import ZoneInfo

from flask import current_app, jsonify

from ...core import Controller
from ...core.models import CarHeaterStatus
from ...extensions import socketio

if TYPE_CHECKING:
    from .car_heater_api import FallbackStatus

logger = logging.getLogger(__name__)

_ALERT_MIN_POWER_W = float(os.getenv("CAR_HEATER_ALERT_MIN_POWER_W", "5"))
_ALERT_NO_POWER_MINUTES = int(os.getenv("CAR_HEATER_ALERT_NO_POWER_MINUTES", "10"))
_ALERT_STUCK_ON_MINUTES = int(os.getenv("CAR_HEATER_ALERT_STUCK_ON_MINUTES", "180"))
_ALERT_SHELLY_MISSING_MINUTES = int(os.getenv("CAR_HEATER_ALERT_SHELLY_MISSING_MINUTES", "15"))


class _CarHeaterAlertState:
    def __init__(self) -> None:
        self.low_power_since: datetime | None = None
        self.heater_on_since: datetime | None = None
        self.last_shelly_ts: datetime | None = None
        self.last_heater_on: bool | None = None


_ALERT_STATE = _CarHeaterAlertState()


def parse_timestamp(raw_ts: str) -> datetime:
    """Parse timestamp string (UTC) and convert to Helsinki time."""
    dt_utc = datetime.strptime(
        raw_ts, "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)
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


def run_keep_at_temp_tick(car: CarHeaterStatus) -> None:
    """Run the keep-at-temperature thermostat tick."""
    try:
        from ...services.car_heater import KeepAtTempService
        svc: KeepAtTempService | None = getattr(
            current_app, "keep_at_temp_service", None
        )
        if svc:
            svc.tick(current_temp=car.ambient_temp, heater_on=car.is_heater_on)
    except Exception as e:
        logger.exception("Failed to run keep-at-temp tick: %s", e)


def process_commands(
    data: Dict[str, Any],
    car: CarHeaterStatus | None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any] | None, Dict[str, Any] | None]:
    """
    Process car heater commands: update charge mode, handle results, fetch queued commands.

    Returns:
        Tuple of (commands, command_status, charge_mode_state)
    """
    commands: List[Dict[str, Any]] = []
    command_status: Dict[str, Any] | None = None
    charge_mode_state: Dict[str, Any] | None = None

    try:
        from ...services.car_heater import CarHeaterService
        from ...services.alert_webhook import record_alert

        service: CarHeaterService | None = getattr(
            current_app, "car_heater_service", None
        )
        if not service:
            return commands, command_status, charge_mode_state

        # Update charge mode from the latest Shelly status
        if car is not None:
            try:
                ts_iso = (
                    car.timestamp.isoformat()
                    if hasattr(car.timestamp, "isoformat")
                    else str(car.timestamp)
                )
                service.handle_status_update(
                    instant_power_w=car.instant_power_w,
                    is_heater_on=car.is_heater_on,
                    timestamp_iso=ts_iso,
                )
            except Exception as e:
                logger.exception(
                    "Failed to update car heater charge mode: %s", e)

        # Handle action results from ESP
        action_results = data.get("action_results", {})
        normalized_results: List[Dict[str, Any]] = []
        if action_results:
            if isinstance(action_results, list):
                for item in action_results:
                    if isinstance(item, dict):
                        if "action" in item:
                            normalized_results.append({
                                "action": item.get("action"),
                                "success": item.get("success", item.get("ok", False)),
                            })
                        else:
                            for key, val in item.items():
                                normalized_results.append({
                                    "action": key,
                                    "success": bool(val),
                                })
                    elif isinstance(item, str):
                        normalized_results.append(
                            {"action": item, "success": True})
            elif isinstance(action_results, dict):
                if "action" in action_results:
                    normalized_results.append({
                        "action": action_results.get("action"),
                        "success": action_results.get("success", action_results.get("ok", False)),
                    })
                else:
                    for key, val in action_results.items():
                        normalized_results.append({
                            "action": key,
                            "success": bool(val),
                        })
            if normalized_results:
                service.mark_command_success(normalized_results)

        # Fetch and mark commands
        try:
            max_queue = int(os.getenv("CAR_HEATER_ALERT_QUEUE_LENGTH", "10"))
            queued_len = len(service.peek_queued_commands())
            if queued_len >= max_queue:
                record_alert(
                    key="car_heater_queue_backlog",
                    title="Car heater command backlog",
                    message=f"queued_commands={queued_len}",
                )
        except Exception:
            pass
        commands = service.get_queued_commands()
        service.mark_commands_sent(commands)

        command_status = asdict(service.get_command_status())
        try:
            charge_mode_state = asdict(service.get_charge_mode_state())
        except Exception:
            charge_mode_state = None

        logger.debug(
            "cmd status: %s charge_mode: %s", command_status, charge_mode_state
        )

    except Exception as e:
        logger.exception("Failed to fetch queued car heater commands: %s", e)

    if commands:
        logger.info("Sending %s commands to car heater ESP", commands)

    return commands, command_status, charge_mode_state


def emit_status_to_views(
    status_payload: Dict[str, Any],
    command_status: Dict[str, Any] | None,
    charge_mode_state: Dict[str, Any] | None,
) -> None:
    """Emit car heater status to connected browser views via Socket.IO."""
    try:
        payload: Dict[str, Any] = {"status": status_payload}
        if command_status is not None:
            payload["command_status"] = command_status
        if charge_mode_state is not None:
            payload["charge_mode"] = charge_mode_state
        socketio.emit("car_heater_status", payload)
    except Exception as e:
        logger.exception(
            "Failed to emit car_heater_status over Socket.IO: %s", e)


def handle_status_update_request(
    data: Dict[str, Any] | None,
    fallback_status: "FallbackStatus",
    commands_enabled: bool = True,
    is_test: bool = False,
) -> Tuple[Any, int]:
    """
    Shared handler for car heater status update requests.

    Args:
        data: JSON payload from request
        fallback_status: Global fallback status object to update
        commands_enabled: Whether to process commands

    Returns:
        Flask response tuple (jsonify response, status code)
    """
    start_time = time.perf_counter()

    ctrl: Controller = getattr(current_app, "ctrl", None)
    if ctrl is None:
        return jsonify({"error": "Controller not initialized"}), 500

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
        # KFactor calibration tick (Ready-by model)
        try:
            from ...services.car_heater import KFactorCalibrator

            ksvc: KFactorCalibrator | None = getattr(
                current_app, "kfactor_calibrator", None
            )
            if ksvc is not None:
                ksvc.tick(
                    car,
                    outside_temp_c=data.get("outside_temp"),
                    wind_m_s=data.get("wind_m_s"),
                    is_test=is_test,
                )
        except Exception as e:
            logger.exception("Failed to run kfactor tick: %s", e)
        # Ready-by scheduler tick (may queue turn_on/turn_off)
        try:
            from ...services.car_heater import ReadyByService
            from dataclasses import asdict

            rsvc: ReadyByService | None = getattr(
                current_app, "ready_by_service", None
            )
            if rsvc is not None:
                rsvc.tick(
                    car,
                    outside_temp_c=data.get("outside_temp"),
                    is_test=is_test,
                )
                # Broadcast Ready-by status to all connected views
                try:
                    schedule = rsvc.get_schedule(as_object=True)
                    socketio.emit("ready_by_status", {
                        "schedule": asdict(schedule) if schedule else None
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.exception("Failed to run ready-by tick: %s", e)
        # Broadcast kFactor status to all connected views
        try:
            if ksvc is not None:
                snapshot = ksvc.get_debug_snapshot()
                socketio.emit("kfactor_status", {
                    "state": snapshot.get("state"),
                    "enabled": snapshot.get("config", {}).get("enabled", True),
                    "cooldown_until": snapshot.get("cooldown_until"),
                    "active_params": snapshot.get("active_params"),
                    "last_session": snapshot.get("last_session"),
                    "session_sample_count": snapshot.get("session_sample_count", 0),
                })
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
    emit_status_to_views(status_payload, command_status, charge_mode_state)

    elapsed_time = time.perf_counter() - start_time
    logger.debug(
        "Processed car heater status update in %.3f seconds", elapsed_time)

    return jsonify(commands), 200


def _process_alerts(
    *,
    car: CarHeaterStatus | None,
    shelly_connected: bool,
    timestamp: datetime,
    is_test: bool,
) -> None:
    if is_test:
        return

    from ...services.alert_webhook import record_alert

    if car is not None:
        _ALERT_STATE.last_shelly_ts = timestamp
        _ALERT_STATE.last_heater_on = bool(car.is_heater_on)

        if car.is_heater_on:
            if _ALERT_STATE.heater_on_since is None:
                _ALERT_STATE.heater_on_since = timestamp
            duration_on = (timestamp - _ALERT_STATE.heater_on_since).total_seconds()
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
                low_power_s = (timestamp - _ALERT_STATE.low_power_since).total_seconds()
                if low_power_s >= _ALERT_NO_POWER_MINUTES * 60:
                    record_alert(
                        key="heater_on_no_power",
                        title="Heater ON but no power draw",
                        message=(
                            f"power_w={power_w:.1f} "
                            f"duration_min={low_power_s/60:.1f}"
                        ),
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
                    message=(
                        f"missing_min={missing_s/60:.1f} "
                        f"last_heater_on={last_heater}"
                    ),
                )
