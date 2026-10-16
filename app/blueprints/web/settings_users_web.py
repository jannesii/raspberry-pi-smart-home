"""User management pages under /settings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...utils import (
    can_delete_user,
    can_edit_user,
    combine_local_date_time,
    flash_error,
    flash_success,
    get_ctrl,
    get_new_password_pair,
    require_admin_or_redirect,
    safe_local_redirect_target,
    validate_password_pair,
)
from . import web_bp

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings/add_user", methods=["GET", "POST"])
@login_required
def add_user():
    ctrl: Controller = get_ctrl()
    guard = require_admin_or_redirect("Sinulla ei ole oikeuksia lisätä käyttäjiä.", "web.user_list")
    if guard:
        return guard
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p, p2 = get_new_password_pair(request.form)
        ok, msg = validate_password_pair(p, p2, required=True, min_len=6)
        if not ok:
            flash_error(msg or "Virheellinen salasana.")
            return render_template("add_user.html")
        is_temp = bool(request.form.get("is_temporary"))
        duration_value = int(request.form.get("duration_value", "1")) if is_temp else None
        duration_unit = request.form.get("duration_unit", "hours") if is_temp else None
        logger.debug("Adding user %s", u)
        try:
            if is_temp:
                logger.info(
                    "Adding temporary user %s (temporary=%s, duration=%s %s)",
                    u,
                    is_temp,
                    duration_value,
                    duration_unit,
                )
                ctrl.create_temporary_user(u, p, duration_value, duration_unit)
                flash_success(f"Väliaikainen käyttäjä «{u}» lisätty onnistuneesti.")
            else:
                ctrl.register_user(u, p)
                flash_success(f"Käyttäjä «{u}» lisätty onnistuneesti.")
            logger.info("User %s added by %s", u, current_user.get_id())
            return redirect(url_for("web.user_list"))
        except ValueError as ve:
            flash_error(str(ve))
            logger.warning("Add user failed: %s", ve)

    return render_template("add_user.html")


@web_bp.route("/settings/users", methods=["GET", "POST"])
@login_required
def user_list():
    ctrl: Controller = get_ctrl()
    guard = require_admin_or_redirect(
        "Sinulla ei ole oikeuksia hallita käyttäjiä.", "web.get_settings_page"
    )
    if guard:
        return guard
    if request.method == "POST":
        username = request.form.get("delete_user")
        if username:
            try:
                user = ctrl.get_user_by_username(username, include_pw=False)
                ok, msg = (
                    can_delete_user(user, current_user.get_id())
                    if user
                    else (False, "Käyttäjää ei löytynyt.")
                )
                if not ok:
                    flash(msg, "error")
                    return redirect(url_for("web.user_list"))
                ctrl.delete_user(username)
                flash(f"Käyttäjä «{username}» poistettu.", "success")
            except Exception as e:
                flash(f"Poisto epäonnistui: {e}", "error")
    users = ctrl.get_all_users(exclude_admin=False, exclude_current=False, exclude_expired=False)
    return render_template("users.html", users=users)


@web_bp.route("/settings/users/edit/<username>", methods=["GET", "POST"])
@login_required
def edit_user(username):
    ctrl: Controller = get_ctrl()
    user = ctrl.get_user_by_username(username, include_pw=False)
    if not current_user.is_admin:
        flash("Sinulla ei ole oikeuksia muokata käyttäjiä.", "error")
        logger.warning(
            "Non-admin user %s attempted to edit user %s", current_user.get_id(), username
        )
        requested_next = request.args.get("next")
        next_url = safe_local_redirect_target(requested_next, url_for("web.user_list"))
        if requested_next and next_url != requested_next:
            logger.warning("Rejected external edit-user redirect target: %r", requested_next)
        return redirect(next_url)
    if not user:
        flash_error("Käyttäjää ei löytynyt.")
        return redirect(url_for("web.user_list"))
    ok, msg = can_edit_user(user)
    if not ok:
        flash_error(msg)
        return redirect(url_for("web.user_list"))
    if request.method == "POST":
        new_username = (request.form.get("username") or "").strip()
        # Support both new and legacy field names for password change
        password = (request.form.get("new_password") or request.form.get("password") or "").strip()
        password_confirm = (
            request.form.get("new_password_confirm") or request.form.get("password_confirm") or ""
        ).strip()
        is_admin = bool(request.form.get("is_admin"))
        is_temporary = bool(request.form.get("is_temporary"))

        expires_date = (request.form.get("expires_date") or "").strip()
        expires_time = (request.form.get("expires_time") or "").strip()
        expires_at: str | None = None
        if is_temporary and (expires_date and expires_time):
            try:
                expires_at = combine_local_date_time(expires_date, expires_time)
            except Exception:
                flash_error("Virheellinen vanhenemisaika.")
                return render_template("edit_user.html", user=user)
        elif is_temporary:
            # Allow temporary without specific expiry (left blank)
            expires_at = None

        # Validate password if provided
        if password:
            ok, msg = validate_password_pair(password, password_confirm, required=True, min_len=6)
            if not ok:
                flash_error(msg or "Virheellinen salasana.")
                return render_template("edit_user.html", user=user)

        try:
            ctrl.update_user(
                current_username=username,
                new_username=new_username or None,
                password=password or None,
                is_temporary=is_temporary,
                is_admin=is_admin,
                expires_at=expires_at,
            )
            flash_success("Käyttäjä päivitetty.")
            return redirect(url_for("web.user_list"))
        except ValueError as ve:
            flash_error(str(ve))
        except Exception as e:
            flash_error(f"Päivitys epäonnistui: {e}")
    return render_template("edit_user.html", user=user)
