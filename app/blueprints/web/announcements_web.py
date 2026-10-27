"""Spoken announcement page under /settings (Root-Admin)."""

from __future__ import annotations

import logging

from flask import render_template
from flask_login import current_user, login_required

from ...utils import require_root_admin_or_redirect
from . import web_bp

logger = logging.getLogger(__name__)


@web_bp.route("/settings/announcements", methods=["GET"])
@login_required
def announcements_page():
    guard = require_root_admin_or_redirect(
        "You do not have permission to manage announcements.",
        "web.get_settings_page",
    )
    if guard:
        return guard
    logger.debug("announcements_page user=%s", current_user.get_id())
    return render_template("announcements.html")
