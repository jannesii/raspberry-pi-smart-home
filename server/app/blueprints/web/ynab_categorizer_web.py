"""YNAB categorizer web page."""

from __future__ import annotations

import logging

from flask import current_app, render_template
from flask_login import current_user, login_required

from ...utils import require_root_admin_or_redirect
from . import web_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/ynab-categorizer", methods=["GET"])
@login_required
def get_ynab_categorizer_page():
    guard = require_root_admin_or_redirect(
        "You do not have permission to access the YNAB categorizer.",
        "web.get_settings_page",
    )
    if guard:
        return guard

    svc = getattr(current_app, "ynab_categorizer_service", None)
    logger.debug("get_ynab_categorizer_page user=%s service=%s", current_user.get_id(), svc)

    configured = bool(svc is not None and svc.is_configured())
    initial_config: dict = {}
    bootstrap_status: dict = {}

    if configured:
        try:
            initial_config = svc.get_config()
            bootstrap_status = svc.get_bootstrap_status()
        except Exception as exc:
            logger.exception("Failed to prepare initial YNAB page payload: %s", exc)

    return render_template(
        "ynab_categorizer.html",
        ynab_configured=configured,
        ynab_initial_config=initial_config,
        ynab_bootstrap_status=bootstrap_status,
    )
