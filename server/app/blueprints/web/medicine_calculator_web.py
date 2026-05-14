"""Medicine calculator page under /settings."""

from __future__ import annotations

import logging

from flask import render_template
from flask_login import current_user, login_required

from ...utils import require_root_admin_or_redirect
from . import web_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings/medicine-calculator", methods=["GET"])
@login_required
def medicine_calculator_page():
    guard = require_root_admin_or_redirect(
        "You do not have permission to access the medicine calculator.",
        "web.get_settings_page",
    )
    if guard:
        return guard

    logger.debug("medicine_calculator_page user=%s", current_user.get_id())
    return render_template("medicine_calculator.html")
