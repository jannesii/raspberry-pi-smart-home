"""Car Heater Blueprint definition.

This module is separate to avoid circular imports.
All route modules should import car_bp from here.
"""

from flask import Blueprint

# Create main car heater blueprint - shared by all submodules
car_bp = Blueprint("car_bp", __name__, url_prefix="/car_heater")
