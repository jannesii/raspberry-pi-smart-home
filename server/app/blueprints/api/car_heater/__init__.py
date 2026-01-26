"""Car Heater API subpackage.

Organizes car heater functionality by feature:
- status.py: Status updates from ESP32, alerts, parsing
- control.py: Manual control, command queuing, history
- kfactor.py: KFactor calibration endpoints
- ready_by.py: Ready-by scheduling endpoints
"""

# Import blueprint first (no dependencies)
# Import route modules to register routes on car_bp
from . import (
    control,
    kfactor,
    ready_by,
    status,
)
from ._blueprint import car_bp

__all__ = ["car_bp"]
