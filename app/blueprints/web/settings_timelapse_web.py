"""Timelapse configuration page under /settings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...utils import (
    check_vals,
    flash_error,
    flash_success,
)
from . import web_bp

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings/timelapse_conf", methods=["GET", "POST"])
@login_required
def timelapse_conf():
    ctrl: Controller = current_app.ctrl  # type: ignore
    vals = {}
    if not current_user.is_admin:
        flash_error("Sinulla ei ole oikeuksia muuttaa asetuksia.")
        logger.warning(
            "Non-admin user %s attempted to change timelapse settings", current_user.get_id()
        )
        return redirect(url_for("web.get_settings_page"))
    if request.method == "POST":
        try:
            vals = {
                "image_delay": int(request.form.get("image_delay", "0")),
                "temphum_delay": int(request.form.get("temphum_delay", "0")) * 60,
                "status_delay": int(request.form.get("status_delay", "0")),
            }

            logger.debug("Timelapse config: %s", vals)
            check_failed = check_vals(**vals)

            if check_failed:
                for c in check_failed:
                    # c['cat'] is a flash category (e.g., 'error', 'warning')
                    flash(c["msg"], c["cat"])
                logger.warning("Invalid timelapse input: %s", check_failed)
                return render_template("timelapse_conf.html", **vals)
            else:
                ctrl.update_timelapse_conf(**vals)
                # Emit only to connected timelapse clients (not to views)
                try:
                    from ...sockets import SocketEventHandler

                    handler = SocketEventHandler(current_app.socketio, ctrl)  # type: ignore
                    handler.emit_to_clients("timelapse_conf", vals)
                except Exception:
                    # Fallback to broadcast if handler unavailable
                    current_app.socketio.emit("timelapse_conf", vals)  # type: ignore
                flash_success("Timelapsen konfiguraatio päivitetty onnistuneesti.")
                logger.info("Timelapse updated %s by %s", vals, current_user.get_id())
                return redirect(url_for("web.get_settings_page"))
        except ValueError as ve:
            flash_error(str(ve))
            logger.warning("Invalid timelapse input: %s", ve)
    else:
        conf = ctrl.get_timelapse_conf()
        if conf:
            vals = {
                "image_delay": conf.image_delay,
                "temphum_delay": int(conf.temphum_delay / 60),
                "status_delay": conf.status_delay,
            }
    return render_template("timelapse_conf.html", **vals)
