"""Car Heater API subpackage.

Organizes car heater functionality by feature:
- status.py: Status updates from ESP32, alerts, parsing
- control.py: Manual control, command queuing, history
- kfactor.py: KFactor calibration endpoints
- ready_by.py: Ready-by scheduling endpoints
"""

from . import ready_by
from . import kfactor
from . import control
from . import status
from flask import Blueprint

# Create main car heater blueprint
car_bp = Blueprint('car_bp', __name__, url_prefix='/car_heater')

# Import and register route modules (they attach routes to car_bp)

__all__ = ['car_bp']
