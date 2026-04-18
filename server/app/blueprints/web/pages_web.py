"""General pages (HTML) routes: home, 3D, temperatures."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from flask import render_template
from flask_login import current_user, login_required

from ...utils import get_ctrl
from . import web_bp

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TEMPERATURES_OUTSIDE_LOCATION_NAMES = ["Rengonharju", "Pelmaa"]


@web_bp.route("/")
@login_required
def get_home_page():
    logger.info("Rendering home for %s", current_user.get_id())
    return render_template("index.html")


@web_bp.route("/3d")
@login_required
def get_3d_page():
    ctrl: Controller = get_ctrl()
    logger.info("Rendering 3D page for %s", current_user.get_id())
    from ...core import TemperatureHumidity

    img = ctrl.get_last_image()
    th = TemperatureHumidity(id=1, timestamp="", temperature=0.0, humidity=0.0)
    st = ctrl.get_last_3d_status()
    logger.debug(f"Last image: {img.id if img else None}, Last temphum: {th}, Last status: {st}")
    return render_template(
        "3d.html",
        last_image=asdict(img) if img else None,
        last_temphum=asdict(th) if th else None,
        last_status=asdict(st) if st else None,
    )


@web_bp.route("/temperatures", methods=["GET"])
@login_required
def get_temperatures_page():
    ctrl: Controller = get_ctrl()
    locations = ctrl.get_unique_locations()
    bootstrap = {
        "locations": locations,
        "outside_location_names": TEMPERATURES_OUTSIDE_LOCATION_NAMES,
        "language": "en",
    }
    logger.info("Rendering temperatures page for %s", current_user.get_id())
    logger.debug(
        "Temperatures bootstrap prepared with %d locations and outside aliases=%s",
        len(locations),
        TEMPERATURES_OUTSIDE_LOCATION_NAMES,
    )
    return render_template("temperatures.html", temperatures_bootstrap=bootstrap)
