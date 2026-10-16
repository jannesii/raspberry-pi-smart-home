import logging
from datetime import datetime
from urllib.parse import urlsplit

import pytz
from flask import current_app, flash, jsonify, redirect, url_for
from flask_login import current_user

from .core import Controller

logger = logging.getLogger(__name__)


def check_vals(image_delay: int, temphum_delay: int, status_delay: int):
    """Check if the values are valid."""
    logger.debug(
        "Checking values: image_delay=%s, temphum_delay=%s, status_delay=%s",
        image_delay,
        temphum_delay,
        status_delay,
    )

    ret = []
    try:
        if image_delay <= 0:
            msg, cat = "Kuvan viiveen on oltava suurempi kuin 0 sekuntia.", "error"
            ret.append({"msg": msg, "cat": cat})
        if temphum_delay <= 0:
            msg, cat = (
                "Lämpötilan ja kosteuden viiveen on oltava suurempi kuin 0 sekuntia.",
                "error",
            )
            ret.append({"msg": msg, "cat": cat})
        if status_delay <= 0:
            msg, cat = "Tilatiedon viiveen on oltava suurempi kuin 0 sekuntia.", "error"
            ret.append({"msg": msg, "cat": cat})
    except ValueError as ve:
        msg, cat = "Virheellinen syöte: " + str(ve), "error"
        ret.append({"msg": msg, "cat": cat})

    return ret


# --- Web helpers to reduce repetition ---


def get_ctrl() -> Controller:
    """Return the shared Controller instance from the Flask app."""
    return current_app.ctrl  # type: ignore


def require_admin_or_redirect(message: str, redirect_endpoint: str):
    """If current user is not admin, flash message and return a redirect.
    Caller should check and return this value if not None.
    """
    if not getattr(current_user, "is_admin", False):
        flash(message, "error")
        logger.warning("Non-admin user %s blocked: %s", current_user.get_id(), message)
        return redirect(url_for(redirect_endpoint))
    return None


def require_root_admin_or_redirect(
    message: str, redirect_endpoint: str | None = None, json: bool = False
):
    """If current user is not root-admin, flash and redirect.
    Caller should check and return this value if not None.
    """
    ctrl: Controller = get_ctrl()
    me = ctrl.get_user_by_username(current_user.get_id(), include_pw=False)
    if not me or not getattr(me, "is_root_admin", False):
        flash(message, "error")
        logger.warning("Non-root-admin user %s blocked: %s", current_user.get_id(), message)
        if json:
            return jsonify({"ok": False, "error": "forbidden", "message": message}), 403
        return redirect(url_for(redirect_endpoint))
    logger.debug("Root-admin user %s authorized", current_user.get_id())
    return None


def safe_local_redirect_target(target: str | None, fallback: str) -> str:
    """Return a local absolute-path redirect target or the supplied fallback."""
    if not target:
        return fallback

    candidate = target.strip()
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
    ):
        logger.debug("Rejected non-local redirect target: %r", target)
        return fallback
    return candidate


def combine_local_date_time(date_str: str, time_str: str, tz_name: str = "Europe/Helsinki") -> str:
    """Combine HTML date (YYYY-MM-DD) and time (HH:MM) into ISO string in given timezone.
    Raises ValueError on invalid input.
    """
    if not date_str or not time_str:
        raise ValueError("Missing date or time")
    fin = pytz.timezone(tz_name)
    dt_naive = datetime.fromisoformat(f"{date_str}T{time_str}")
    dt_local = fin.localize(dt_naive)
    return dt_local.isoformat()


def can_edit_user(user) -> tuple[bool, str | None]:
    """Return False and a message if the target user cannot be edited."""
    if getattr(user, "is_root_admin", False):
        return False, "Et voi muokata Root-Admin tiliä."
    return True, None


def can_delete_user(target_user, acting_user_id: str) -> tuple[bool, str | None]:
    """Return False and a message if the target user cannot be deleted."""
    if getattr(target_user, "is_root_admin", False):
        return False, "Et voi poistaa Root-Admin tiliä."
    if getattr(target_user, "username", None) == acting_user_id:
        return False, "Et voi poistaa omaa tiliäsi."
    return True, None


# --- Flash helpers ---


def flash_success(message: str):
    flash(message, "success")


def flash_error(message: str):
    flash(message, "error")


def flash_warning(message: str):
    flash(message, "warning")


def flash_info(message: str):
    flash(message, "info")


# --- Password helpers ---


def get_new_password_pair(form) -> tuple[str, str]:
    """Extract new password and confirmation from a form, supporting legacy names.
    Returns (password, confirm) trimmed; missing values become ''.
    Supported names:
      - password, password_confirm
      - new_password, new_password_confirm
      - new_password, confirm_password
    """
    pw = (form.get("new_password") or form.get("password") or "").strip()
    conf = (
        form.get("new_password_confirm")
        or form.get("password_confirm")
        or form.get("confirm_password")
        or ""
    ).strip()
    return pw, conf


def validate_password_pair(
    password: str, confirm: str, *, required: bool = False, min_len: int = 6
) -> tuple[bool, str | None]:
    """Validate a password + confirmation pair.
    - If required is False and both empty, returns (True, None).
    - If required is True, both must be non-empty.
    - If provided, they must match and meet min length.
    Returns (ok, error_message).
    """
    if not password and not confirm:
        return (not required), (None if not required else "Salasana ja vahvistus vaaditaan.")
    if required and (not password or not confirm):
        return False, "Salasana ja vahvistus vaaditaan."
    if password != confirm:
        return False, "Uusi salasana ja vahvistus eivät täsmää."
    if len(password) < min_len:
        return False, f"Uuden salasanan on oltava vähintään {min_len} merkkiä."
    return True, None
