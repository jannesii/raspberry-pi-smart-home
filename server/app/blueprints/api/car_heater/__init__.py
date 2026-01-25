"""Car Heater API subpackage.

Organizes car heater functionality by feature:
- status.py: Status updates from ESP32, alerts, parsing
- control.py: Manual control, command queuing, history
- kfactor.py: KFactor calibration endpoints
- ready_by.py: Ready-by scheduling endpoints
"""

# Import blueprint first (no dependencies)
from ._blueprint import car_bp

# Import route modules to register routes on car_bp
from . import status  # noqa: E402, F401
from . import control  # noqa: E402, F401
from . import kfactor  # noqa: E402, F401
from . import ready_by  # noqa: E402, F401

__all__ = ['car_bp']
