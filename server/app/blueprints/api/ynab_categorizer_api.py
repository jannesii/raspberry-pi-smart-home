"""YNAB categorizer API routes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from ...utils import require_root_admin_or_redirect

if TYPE_CHECKING:
    from ...services.ynab import YnabCategorizerService

ynab_categorizer_bp = Blueprint("api_ynab_categorizer", __name__, url_prefix="/ynab-categorizer")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _service_not_configured_response():
    return (
        jsonify(
            {
                "ok": False,
                "error": "not_configured",
                "message": "YNAB categorizer service is not configured",
            }
        ),
        503,
    )


def _get_service() -> None | YnabCategorizerService:
    svc: YnabCategorizerService | None = getattr(current_app, "ynab_categorizer_service", None)
    logger.debug("_get_service resolved=%s", svc)
    if svc is None:
        return None
    try:
        if not svc.is_configured():
            return None
    except Exception:
        logger.exception("YNAB service is_configured failed")
        return None
    return svc


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("Invalid boolean value")


def _parse_int(value: Any, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("Invalid integer value")
        return default
    if isinstance(value, bool):
        raise ValueError("Invalid integer value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("Invalid integer value")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            if default is None:
                raise ValueError("Invalid integer value")
            return default
        try:
            return int(stripped)
        except ValueError as exc:
            raise ValueError("Invalid integer value") from exc
    raise ValueError("Invalid integer value")


@ynab_categorizer_bp.route("/queue", methods=["GET"])
@login_required
def ynab_queue():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    queue_filter_mode = (request.args.get("queue_filter_mode") or "").strip() or None
    logger.debug("ynab_queue called user=%s mode=%s", current_user.get_id(), queue_filter_mode)
    try:
        payload = svc.get_queue(queue_filter_mode=queue_filter_mode)
        return jsonify({"ok": True, **payload})
    except ValueError as exc:
        logger.warning("ynab_queue bad request: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("ynab_queue failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "queue_failed", "message": "Failed to fetch queue"}
        ), 500


@ynab_categorizer_bp.route("/apply", methods=["POST"])
@login_required
def ynab_apply():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    data = request.get_json(silent=True) or {}
    tx_ids_raw = data.get("transaction_ids")
    category_id = str(data.get("category_id") or "").strip()

    if not isinstance(tx_ids_raw, list):
        return (
            jsonify(
                {"ok": False, "error": "bad_request", "message": "transaction_ids must be a list"}
            ),
            400,
        )

    tx_ids = [str(item).strip() for item in tx_ids_raw if str(item).strip()]
    if not tx_ids:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "bad_request",
                    "message": "transaction_ids must not be empty",
                }
            ),
            400,
        )
    if len(tx_ids) > 200:
        return (
            jsonify(
                {"ok": False, "error": "bad_request", "message": "Max 200 transactions per apply"}
            ),
            400,
        )
    if not category_id:
        return jsonify(
            {"ok": False, "error": "bad_request", "message": "category_id is required"}
        ), 400

    logger.debug(
        "ynab_apply called user=%s tx_count=%s category_id=%s",
        current_user.get_id(),
        len(tx_ids),
        category_id,
    )
    try:
        result = svc.apply_category(
            transaction_ids=tx_ids,
            category_id=category_id,
            applied_by_username=current_user.get_id(),
        )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        logger.warning("ynab_apply bad request: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("ynab_apply failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "apply_failed", "message": "Failed to apply category"}
        ), 500


@ynab_categorizer_bp.route("/bootstrap", methods=["POST"])
@login_required
def ynab_bootstrap():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    data = request.get_json(silent=True) or {}
    try:
        force = _parse_bool(data.get("force"), default=False)
    except ValueError:
        return jsonify({"ok": False, "error": "bad_request", "message": "Invalid force value"}), 400

    logger.debug("ynab_bootstrap called user=%s force=%s", current_user.get_id(), force)
    try:
        result = svc.bootstrap(force=force)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("ynab_bootstrap failed: %s", exc)
        return (
            jsonify({"ok": False, "error": "bootstrap_failed", "message": "Failed to bootstrap"}),
            500,
        )


@ynab_categorizer_bp.route("/bootstrap-status", methods=["GET"])
@login_required
def ynab_bootstrap_status():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    logger.debug("ynab_bootstrap_status called user=%s", current_user.get_id())
    try:
        result = svc.get_bootstrap_status()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("ynab_bootstrap_status failed: %s", exc)
        return (
            jsonify(
                {"ok": False, "error": "status_failed", "message": "Failed to get bootstrap status"}
            ),
            500,
        )


@ynab_categorizer_bp.route("/config", methods=["GET"])
@login_required
def ynab_get_config():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    logger.debug("ynab_get_config called user=%s", current_user.get_id())
    try:
        result = svc.get_config()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("ynab_get_config failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "config_failed", "message": "Failed to fetch config"}
        ), 500


@ynab_categorizer_bp.route("/config", methods=["POST"])
@login_required
def ynab_set_config():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard

    svc = _get_service()
    if svc is None:
        return _service_not_configured_response()

    data = request.get_json(silent=True) or {}
    queue_filter_mode = str(data.get("queue_filter_mode") or "").strip()
    if not queue_filter_mode:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "bad_request",
                    "message": "queue_filter_mode is required",
                }
            ),
            400,
        )
    try:
        show_reconciled_transactions = _parse_bool(
            data.get("show_reconciled_transactions"), default=False
        )
        queue_limit_enabled = _parse_bool(data.get("queue_limit_enabled"), default=False)
        queue_limit_value = _parse_int(data.get("queue_limit_value"), default=30)
        queue_limit_unit = str(data.get("queue_limit_unit") or "days").strip().lower()
    except ValueError as exc:
        logger.warning("ynab_set_config bad request parse error: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400

    logger.debug(
        (
            "ynab_set_config called user=%s queue_filter_mode=%s "
            "show_reconciled_transactions=%s queue_limit_enabled=%s "
            "queue_limit_value=%s queue_limit_unit=%s"
        ),
        current_user.get_id(),
        queue_filter_mode,
        show_reconciled_transactions,
        queue_limit_enabled,
        queue_limit_value,
        queue_limit_unit,
    )
    try:
        result = svc.set_config(
            queue_filter_mode=queue_filter_mode,
            show_reconciled_transactions=show_reconciled_transactions,
            queue_limit_enabled=queue_limit_enabled,
            queue_limit_value=queue_limit_value,
            queue_limit_unit=queue_limit_unit,
        )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        logger.warning("ynab_set_config bad request: %s", exc)
        return jsonify({"ok": False, "error": "bad_request", "message": str(exc)}), 400
    except Exception as exc:
        logger.exception("ynab_set_config failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "config_failed", "message": "Failed to update config"}
        ), 500
