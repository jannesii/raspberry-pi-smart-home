"""Installation & devices: the owner's saved record plus reported device facts.

Device facts come only from live reports (relay capability, sensor tracker).
Sensor models and I2C addresses are the firmware's compiled defaults
(``ESP32C3-Car-Heater/include/io/sensorconfig.h``); the device does not report
them, so the page labels them as defaults.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from ....core._controller.car_heater_installation import INSTALLATION_FIELDS
from ._blueprint import car_bp
from .status import _utc_now, get_car_heater_sensor_tracker

logger = logging.getLogger(__name__)

MAX_FIELD_LENGTH = 500
# Firmware the bounded protocol was tested with (ESP32C3-Car-Heater
# include/core/version.h); shown for comparison, never enforced.
TESTED_FIRMWARE_VERSION = "3.1.1"

SENSORS = (
    ("glass_reference", "Glass reference", "TMP117", "0x48"),
    ("glass_comparison", "Glass comparison", "TMP117", "0x49"),
    ("cabin", "Cabin", "SHT45", "0x44"),
)


def _sensor_facts() -> list[dict[str, Any]]:
    live = get_car_heater_sensor_tracker().snapshot(_utc_now())
    facts = []
    for key, label, model, address in SENSORS:
        reading = live.get(key) or {}
        facts.append(
            {
                "key": key,
                "label": label,
                "model": model,
                "default_address": address,
                "state": reading.get("state", "missing"),
                "temperature_c": reading.get("temperature_c"),
                "sampled_at": reading.get("sampled_at"),
                "age_s": reading.get("age_s"),
            }
        )
    return facts


def _device_facts() -> dict[str, Any]:
    service = getattr(current_app, "car_heater_service", None)
    cap = None
    if service is not None:
        try:
            cap = service.get_device_capability()
        except Exception:
            logger.warning("Relay capability unavailable for installation view", exc_info=True)
    reported = cap.get("firmware_version") if cap else None
    try:
        sensors = _sensor_facts()
    except Exception:
        logger.warning("Sensor readings unavailable for installation view", exc_info=True)
        sensors = [
            {
                "key": key,
                "label": label,
                "model": model,
                "default_address": address,
                "state": "missing",
                "temperature_c": None,
                "sampled_at": None,
                "age_s": None,
            }
            for key, label, model, address in SENSORS
        ]
    return {
        "firmware": {
            "reported": reported,
            "tested": TESTED_FIRMWARE_VERSION,
            "matches": None if reported is None else reported == TESTED_FIRMWARE_VERSION,
        },
        "relay": {
            "reported": cap is not None,
            "protocol": cap.get("protocol") if cap else None,
            "ready": (cap.get("ready") is True) if cap else None,
            "note": cap.get("note") if cap else None,
            "age_s": cap.get("age_s") if cap else None,
        },
        "sensors": sensors,
    }


def _record(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in ("saved_at", "saved_by", *INSTALLATION_FIELDS)}


def _validated(data: Any) -> dict[str, str | None]:
    if not isinstance(data, dict) or not data:
        raise ValueError("Expected a JSON object with the fields to save")
    unknown = set(data) - set(INSTALLATION_FIELDS)
    if unknown:
        raise ValueError(f"Unknown installation field: {sorted(unknown)[0]}")
    changes: dict[str, str | None] = {}
    for key, value in data.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        text = (value or "").strip()
        if len(text) > MAX_FIELD_LENGTH:
            raise ValueError(f"{key} must be at most {MAX_FIELD_LENGTH} characters")
        changes[key] = text or None
    return changes


def installation_payload() -> dict[str, Any]:
    """Device facts stay available even when the saved record cannot be read."""
    record_available = True
    try:
        history = current_app.ctrl.get_car_heater_installation_history()
    except Exception:
        logger.exception("Installation record unreadable")
        history, record_available = [], False
    return {
        "record_available": record_available,
        "record": _record(history[0]) if history else None,
        "history": [_record(row) for row in history],
        "devices": _device_facts(),
    }


@car_bp.route("/installation", methods=["GET", "PATCH"])
@login_required
def installation_settings():
    if request.method == "PATCH":
        if not getattr(current_user, "is_admin", False):
            logger.warning("Non-admin %s refused installation change", current_user.get_id())
            return jsonify(error="Only administrators can change the installation record"), 403
        try:
            changes = _validated(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            current_app.ctrl.save_car_heater_installation(changes, saved_by=current_user.get_id())
        except Exception:
            logger.exception("Saving the installation record failed")
            return jsonify(error="Installation record was not saved"), 500
    try:
        return jsonify(installation_payload())
    except Exception:
        logger.exception("Installation view unavailable")
        return jsonify(error="Installation details are unavailable"), 500
