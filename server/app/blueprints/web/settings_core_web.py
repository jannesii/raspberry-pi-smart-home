"""Core settings pages: settings index and password change."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...utils import (
    flash_error,
    flash_success,
    get_ctrl,
    get_new_password_pair,
    validate_password_pair,
)
from . import web_bp

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings")
@login_required
def get_settings_page():
    logger.info("Rendering settings for %s", current_user.get_id())
    return render_template("settings.html")


@web_bp.route("/settings/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    ctrl: Controller = get_ctrl()
    # Root-Admin cannot change password (by requirement)
    me = ctrl.get_user_by_username(current_user.get_id(), include_pw=False)
    if me and getattr(me, "is_root_admin", False):
        flash_error("Root-Admin ei voi vaihtaa salasanaa.")
        return redirect(url_for("web.get_settings_page"))

    if request.method == "POST":
        current_pw = (request.form.get("current_password") or "").strip()
        new_pw, confirm_pw = get_new_password_pair(request.form)

        # Validate input
        if not current_pw:
            flash_error("Syötä nykyinen salasana.")
            return render_template("change_password.html")
        ok, msg = validate_password_pair(new_pw, confirm_pw, required=True, min_len=6)
        if not ok:
            flash_error(msg or "Virheellinen salasana.")
            return render_template("change_password.html")

        # Verify current password
        if not ctrl.authenticate_user(current_user.get_id(), current_pw):
            flash_error("Nykyinen salasana on virheellinen.")
            return render_template("change_password.html")

        # Update password
        try:
            ctrl.update_user(current_username=current_user.get_id(), password=new_pw)
            ctrl.log_message(
                log_type="info", message=f"User {current_user.get_id()} changed password"
            )
            flash_success("Salasana vaihdettu.")
            return redirect(url_for("web.get_settings_page"))
        except Exception as e:
            flash_error(f"Salasanan vaihto epäonnistui: {e}")
            return render_template("change_password.html")

    return render_template("change_password.html")
