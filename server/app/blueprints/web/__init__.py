"""Web (HTML) routes aggregator.

Defines the `web` blueprint and imports per-area modules that attach
handlers via `@web_bp.route`.
"""

from flask import Blueprint

# Define the web blueprint. Route modules below will attach handlers to this.
web_bp = Blueprint("web", __name__)

# Import route modules so that their @web_bp.route handlers register.
# These imports are intentional for side effects.
from . import (
    car_heater_web,
    pages_web,
    settings_core_web,
    settings_logging_web,
    settings_novpn_web,
    settings_ops_web,
    settings_timelapse_web,
    settings_users_web,
    ynab_categorizer_web,
)
