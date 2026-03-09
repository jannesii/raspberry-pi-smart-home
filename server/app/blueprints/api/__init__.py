"""API aggregator for versioned routes.

This module defines the top-level `/api` blueprint and registers
per-domain sub-blueprints to keep route modules small and focused.
"""

from flask import Blueprint

from .bmp_sensor import bmp_bp
from .car_heater import car_bp
from .esp32_api import esp32_bp
from .hvac_api import hvac_bp
from .misc_api import misc_bp
from .novpn_api import novpn_bp
from .timelapse_api import timelapse_bp
from .ynab_categorizer_api import ynab_categorizer_bp

api_bp = Blueprint("api", __name__, url_prefix="/api")


# Register all sub-blueprints under /api
api_bp.register_blueprint(bmp_bp)
api_bp.register_blueprint(esp32_bp)
api_bp.register_blueprint(novpn_bp)
api_bp.register_blueprint(hvac_bp)
api_bp.register_blueprint(timelapse_bp)
api_bp.register_blueprint(misc_bp)
api_bp.register_blueprint(car_bp)
api_bp.register_blueprint(ynab_categorizer_bp)
