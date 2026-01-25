"""Logging control page under /settings.

Allows admins to change Python logging levels for loggers and handlers at runtime.
Persisted in DB and applied on startup for this process.
"""

from __future__ import annotations

import logging

from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user

from ...utils import (
    get_ctrl,
    require_admin_or_redirect,
    flash_error,
    flash_success,
)
from ...logging_control import (
    ALLOWED_LEVELS,
    apply_logging_control_config,
    snapshot_logging_state,
)

from . import web_bp


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings/logging/apply", methods=["POST"])
@login_required
def logging_settings_apply():
    """Apply a single logging override change (AJAX)."""
    if not getattr(current_user, "is_admin", False):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    level_name = str(payload.get("level") or "").strip().upper()

    if kind not in {"root", "logger", "handler"}:
        return jsonify({"ok": False, "error": "bad_request", "message": "Invalid kind"}), 400

    if kind in {"logger", "handler"} and not name:
        return jsonify({"ok": False, "error": "bad_request", "message": "Missing name"}), 400

    if level_name and level_name not in ALLOWED_LEVELS:
        return jsonify({"ok": False, "error": "bad_request", "message": "Invalid level"}), 400

    ctrl = get_ctrl()
    cfg = ctrl.get_logging_control_config() or {"logger_levels": {}, "handler_levels": {}}
    if not isinstance(cfg, dict):
        cfg = {"logger_levels": {}, "handler_levels": {}}
    cfg.setdefault("logger_levels", {})
    cfg.setdefault("handler_levels", {})

    # Apply delta (empty level means remove override)
    if kind == "root":
        if level_name:
            cfg["root_level"] = level_name
        else:
            cfg.pop("root_level", None)
    elif kind == "logger":
        logger_levels = cfg.get("logger_levels")
        if not isinstance(logger_levels, dict):
            logger_levels = {}
            cfg["logger_levels"] = logger_levels
        if level_name:
            logger_levels[name] = level_name
        else:
            logger_levels.pop(name, None)
    elif kind == "handler":
        handler_levels = cfg.get("handler_levels")
        if not isinstance(handler_levels, dict):
            handler_levels = {}
            cfg["handler_levels"] = handler_levels
        if level_name:
            handler_levels[name] = level_name
        else:
            handler_levels.pop(name, None)

    try:
        ctrl.set_logging_control_config(cfg)
        apply_logging_control_config(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": "apply_failed", "message": str(e)}), 500

    return jsonify({"ok": True})


@web_bp.route("/settings/logging", methods=["GET", "POST"])
@login_required
def logging_settings():
    guard = require_admin_or_redirect(
        "You do not have permission to change logging settings.", "web.get_settings_page"
    )
    if guard:
        return guard

    ctrl = get_ctrl()

    if request.method == "POST":
        if "reset" in request.form:
            try:
                ctrl.clear_logging_control_config()
                flash_success("Logging overrides cleared. (Restart the service if needed.)")
            except Exception as e:
                flash_error(f"Failed to reset logging overrides: {e}")
            return redirect(url_for("web.logging_settings"))

        # Legacy bulk-save fallback (kept for non-JS clients)
        cfg: dict = {"logger_levels": {}, "handler_levels": {}}

        root_level = (request.form.get("root_level") or "").strip().upper()
        if root_level and root_level in ALLOWED_LEVELS:
            cfg["root_level"] = root_level

        for key, value in request.form.items():
            if not isinstance(key, str):
                continue
            if key.startswith("logger_level|"):
                name = key.split("|", 1)[1]
                level_name = (value or "").strip().upper()
                if level_name and level_name in ALLOWED_LEVELS:
                    cfg["logger_levels"][name] = level_name
            elif key.startswith("handler_level|"):
                hkey = key.split("|", 1)[1]
                level_name = (value or "").strip().upper()
                if level_name and level_name in ALLOWED_LEVELS:
                    cfg["handler_levels"][hkey] = level_name

        try:
            ctrl.set_logging_control_config(cfg)
            apply_logging_control_config(cfg)
            flash_success("Logging settings updated.")
            logger.info("Logging overrides updated by %s", current_user.get_id())
            return redirect(url_for("web.logging_settings"))
        except Exception as e:
            flash_error(f"Failed to update logging settings: {e}")

    state = snapshot_logging_state()
    saved_cfg = ctrl.get_logging_control_config() or {}
    return render_template(
        "logging_settings.html",
        state=state,
        saved_cfg=saved_cfg,
    )
