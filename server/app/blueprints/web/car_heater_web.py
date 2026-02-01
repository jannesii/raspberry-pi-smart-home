"""Car heater control and status page."""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING

from flask import current_app, render_template
from flask_login import current_user, login_required

from ...utils import get_ctrl
from ..api.car_heater.status import fallback_status
from . import web_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if TYPE_CHECKING:
    from ...core import CarHeaterStatus, Controller
    from ...services.car_heater import (
        CarHeaterService,
        KeepAtTempSettings,
        KFactorCalibrator,
        ReadyByService,
    )
    from ...services.weather import WeatherService


@web_bp.route("/car_heater", methods=["GET"])
@login_required
def get_car_heater_page():
    """Render the car heater dashboard page."""
    ctrl: Controller = get_ctrl()
    logger.debug("get_car_heater_page called user=%s", current_user.get_id())
    logger.info("Rendering car heater page for %s", current_user.get_id())

    logger.debug("Fallback shelly_connected=%s", fallback_status.shelly_connected)
    if fallback_status.shelly_connected:
        last: CarHeaterStatus | None = ctrl.get_last_car_heater_status()
        logger.debug("Fetched last car heater status: %s", last)
        last_status = asdict(last) if last is not None else None
    else:
        if is_dataclass(fallback_status):
            last_status = asdict(fallback_status)
        else:
            last_status = {
                "timestamp": fallback_status.timestamp,
                "ambient_temp": fallback_status.ambient_temp,
                "shelly_connected": fallback_status.shelly_connected,
            }
        logger.debug("Using fallback status payload: %s", last_status)

    # Command status from in-memory CarHeaterService (if available)
    command_status = None
    charge_mode_state = None
    try:
        svc: CarHeaterService | None = getattr(current_app, "car_heater_service", None)
        if svc is not None:
            command_status = asdict(svc.get_command_status())
            logger.debug("Car heater command status: %s", command_status)
            try:
                charge_mode_state = asdict(svc.get_charge_mode_state())
                logger.debug("Car heater charge mode state: %s", charge_mode_state)
            except Exception:
                charge_mode_state = None
    except Exception as e:
        logger.exception("Failed to get car heater command status: %s", e)

    # Keep-at-temp settings from database
    keep_at_temp_settings: KeepAtTempSettings = ctrl.get_keep_at_temp_settings()
    logger.debug("Keep-at-temp settings: %s", keep_at_temp_settings)
    # Ready-by schedule (if available)
    ready_by_data = None
    try:
        ready_by_svc: ReadyByService | None = getattr(current_app, "ready_by_service", None)
        ready_by_data = ready_by_svc.ready_by_payload
        logger.debug("Ready-by schedule data: %s", ready_by_data)
    except Exception as e:
        logger.exception("Failed to get ready-by schedule: %s", e)

    # kFactor calibration status (if available)
    kfactor_status = None
    try:
        kfactor_svc: KFactorCalibrator | None = getattr(current_app, "kfactor_calibrator", None)
        if kfactor_svc is not None:
            snapshot = kfactor_svc.get_debug_snapshot()
            # Include full config for advanced settings UI
            full_config = snapshot.get("config", {})
            enabled_value = bool(full_config.get("enabled", False))
            kfactor_status = {
                "state": snapshot.get("state"),
                "autonomous_enabled": enabled_value,
                "enabled": enabled_value,
                "cooldown_until": snapshot.get("cooldown_until"),
                "active_params": snapshot.get("active_params"),
                "last_session": snapshot.get("last_session"),
                "config": full_config,
            }
            logger.debug("kfactor_status prepared: %s", kfactor_status)
    except Exception as e:
        logger.debug("Failed to get kfactor status: %s", e)

    # Weather data
    weather_data = None
    try:
        weather_svc: WeatherService | None = getattr(current_app, "weather_service", None)
        if weather_svc is not None:
            wd = weather_svc.get_latest()
            if wd:
                weather_data = {
                    "outside_temp_c": wd.t2m.value if wd.t2m else None,
                    "wind_speed_mps": wd.ws_10min.value if wd.ws_10min else None,
                    "humidity_pct": wd.rh.value if wd.rh else None,
                    "station_name": wd.station_name,
                }
                logger.debug("Weather data payload: %s", weather_data)
    except Exception as e:
        logger.debug("Failed to get weather data: %s", e)

    return render_template(
        "car_heater.html",
        last_status=last_status,
        command_status=command_status,
        charge_mode_state=charge_mode_state,
        keep_at_temp_settings=asdict(keep_at_temp_settings),
        ready_by_schedule=ready_by_data,
        kfactor_status=kfactor_status,
        weather_data=weather_data,
    )
