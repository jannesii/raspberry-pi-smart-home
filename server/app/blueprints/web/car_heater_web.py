"""Car heater control and status page."""
from __future__ import annotations

import logging
from dataclasses import asdict

from flask import render_template, current_app
from flask_login import login_required, current_user

from ...utils import get_ctrl
from ...core import Controller, CarHeaterStatus
from ...services.car_heater import CarHeaterService
from ...services.car_heater.car_heater_models import KeepAtTempSettings
from ..api.car_heater_api import fallback_status

from . import web_bp


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route('/car_heater', methods=['GET'])
@login_required
def get_car_heater_page():
    """Render the car heater dashboard page."""
    ctrl: Controller = get_ctrl()
    logger.info("Rendering car heater page for %s", current_user.get_id())

    if fallback_status.shelly_connected:
        last: CarHeaterStatus | None = ctrl.get_last_car_heater_status()
        last_status = asdict(last) if last is not None else None
    else:
        last_status = asdict(fallback_status)

    # Command status from in-memory CarHeaterService (if available)
    command_status = None
    charge_mode_state = None
    try:
        svc: CarHeaterService | None = current_app.config.get(
            "CAR_HEATER_SERVICE")
        if svc is not None:
            command_status = asdict(svc.get_command_status())
            try:
                charge_mode_state = asdict(svc.get_charge_mode_state())
            except Exception:
                charge_mode_state = None
    except Exception as e:
        logger.exception("Failed to get car heater command status: %s", e)

    # Keep-at-temp settings from database
    keep_at_temp_settings: KeepAtTempSettings = ctrl.get_keep_at_temp_settings()

    return render_template(
        'car_heater.html',
        last_status=last_status,
        command_status=command_status,
        charge_mode_state=charge_mode_state,
        keep_at_temp_settings=asdict(keep_at_temp_settings),
    )
