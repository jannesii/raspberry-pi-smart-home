"""Medicine calculator API routes."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ...utils import get_ctrl, require_root_admin_or_redirect

if TYPE_CHECKING:
    from ...core import MedicinePurchase

medicine_calculator_bp = Blueprint(
    "api_medicine_calculator", __name__, url_prefix="/medicine-calculator"
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _parse_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _parse_dosing_weekdays(value: Any) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("dosing_weekdays must be a list")
    weekdays: list[int] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("dosing_weekdays must contain weekday numbers")
        try:
            weekday = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("dosing_weekdays must contain weekday numbers") from exc
        if weekday < 0 or weekday > 6:
            raise ValueError("dosing_weekdays values must be between 0 and 6")
        weekdays.append(weekday)
    if not weekdays:
        raise ValueError("At least one dosing weekday is required")
    return sorted(set(weekdays))


def _parse_purchase_payload(data: dict[str, Any]) -> dict[str, Any]:
    medicine_name = str(data.get("medicine_name") or "").strip()
    purchase_date = str(data.get("purchase_date") or "").strip()
    if not medicine_name:
        raise ValueError("medicine_name is required")
    if not purchase_date:
        raise ValueError("purchase_date is required")
    parsed = {
        "medicine_name": medicine_name,
        "purchase_date": purchase_date,
        "pieces_bought": _parse_positive_int(data.get("pieces_bought"), "pieces_bought"),
        "dose_per_dosing_day": _parse_positive_int(
            data.get("dose_per_dosing_day"), "dose_per_dosing_day"
        ),
        "dosing_weekdays": _parse_dosing_weekdays(data.get("dosing_weekdays")),
    }
    logger.debug("_parse_purchase_payload parsed=%s", parsed)
    return parsed


def _purchase_payload(ctrl, purchase: MedicinePurchase) -> dict[str, Any]:
    payload = asdict(purchase)
    payload["dosing_weekdays"] = json.loads(purchase.dosing_weekdays_json or "[]")
    payload["calculation"] = asdict(ctrl.calculate_medicine_purchase(purchase))
    return payload


def _full_payload(ctrl) -> dict[str, Any]:
    purchases = ctrl.list_medicine_purchases()
    payload = {
        "purchases": [_purchase_payload(ctrl, purchase) for purchase in purchases],
        "medicines": ctrl.get_medicine_names(),
        "summaries": ctrl.get_medicine_summaries(),
    }
    logger.debug(
        "_full_payload purchases=%s medicines=%s summaries=%s",
        len(payload["purchases"]),
        len(payload["medicines"]),
        len(payload["summaries"]),
    )
    return payload


@medicine_calculator_bp.route("/purchases", methods=["GET"])
@login_required
def list_medicine_purchases():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    ctrl = get_ctrl()
    logger.debug("list_medicine_purchases called user=%s", current_user.get_id())
    try:
        return jsonify({"ok": True, **_full_payload(ctrl)})
    except Exception as exc:
        logger.exception("list_medicine_purchases failed: %s", exc)
        return jsonify(
            {
                "ok": False,
                "error": "list_failed",
                "message": "Failed to load medicine purchases",
            }
        ), 500


@medicine_calculator_bp.route("/purchases", methods=["POST"])
@login_required
def create_medicine_purchase():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    try:
        parsed = _parse_purchase_payload(data)
    except ValueError as exc:
        logger.warning("create_medicine_purchase bad request: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400

    ctrl = get_ctrl()
    logger.debug("create_medicine_purchase called user=%s parsed=%s", current_user.get_id(), parsed)
    try:
        purchase = ctrl.create_medicine_purchase(**parsed)
        return jsonify(
            {"ok": True, "purchase": _purchase_payload(ctrl, purchase), **_full_payload(ctrl)}
        )
    except ValueError as exc:
        logger.warning("create_medicine_purchase validation failed: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("create_medicine_purchase failed: %s", exc)
        return jsonify(
            {
                "ok": False,
                "error": "create_failed",
                "message": "Failed to save medicine purchase",
            }
        ), 500


@medicine_calculator_bp.route("/purchases/<int:purchase_id>", methods=["PATCH"])
@login_required
def update_medicine_purchase(purchase_id: int):
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    try:
        parsed = _parse_purchase_payload(data)
    except ValueError as exc:
        logger.warning("update_medicine_purchase bad request: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400

    ctrl = get_ctrl()
    logger.debug(
        "update_medicine_purchase called user=%s purchase_id=%s parsed=%s",
        current_user.get_id(),
        purchase_id,
        parsed,
    )
    try:
        purchase = ctrl.update_medicine_purchase(purchase_id, **parsed)
        return jsonify(
            {"ok": True, "purchase": _purchase_payload(ctrl, purchase), **_full_payload(ctrl)}
        )
    except KeyError:
        return jsonify(
            {
                "ok": False,
                "error": "not_found",
                "message": "Medicine purchase not found",
            }
        ), 404
    except ValueError as exc:
        logger.warning("update_medicine_purchase validation failed: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("update_medicine_purchase failed: %s", exc)
        return jsonify(
            {
                "ok": False,
                "error": "update_failed",
                "message": "Failed to update medicine purchase",
            }
        ), 500


@medicine_calculator_bp.route("/purchases/<int:purchase_id>", methods=["DELETE"])
@login_required
def delete_medicine_purchase(purchase_id: int):
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    ctrl = get_ctrl()
    logger.debug(
        "delete_medicine_purchase called user=%s purchase_id=%s",
        current_user.get_id(),
        purchase_id,
    )
    try:
        deleted = ctrl.delete_medicine_purchase(purchase_id)
        if not deleted:
            return jsonify(
                {
                    "ok": False,
                    "error": "not_found",
                    "message": "Medicine purchase not found",
                }
            ), 404
        return jsonify({"ok": True, **_full_payload(ctrl)})
    except Exception as exc:
        logger.exception("delete_medicine_purchase failed: %s", exc)
        return jsonify(
            {
                "ok": False,
                "error": "delete_failed",
                "message": "Failed to delete medicine purchase",
            }
        ), 500
