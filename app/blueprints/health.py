"""Readiness endpoint for release deployment.

``scripts/release.py`` polls ``/healthz`` after starting a release. It is
public (it is read before anyone can log in), read-only and has no device,
notification or automation effect. It exposes only the release revision and
whether the database answers.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from app.release_identity import release_revision

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)

# Resolved once at startup: the release this process was started from, even if
# the release root's ``current`` link is switched later.
RELEASE_REVISION = release_revision(Path(__file__).resolve().parents[2])


@health_bp.route("/healthz")
def healthz():
    available = current_app.ctrl.database_available()  # type: ignore[attr-defined]
    database = {True: "ok", False: "unavailable", None: "unconfigured"}[available]
    if database != "ok":
        logger.warning("Readiness check: database is %s", database)
    return jsonify({"revision": RELEASE_REVISION, "database": database}), (
        200 if database == "ok" else 503
    )
