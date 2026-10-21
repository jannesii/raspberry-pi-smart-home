"""Car Heater API subpackage.

Organizes car heater functionality by feature:
- status.py: Status updates from ESP32, alerts, parsing
- control.py: Manual control, command queuing, history
- kfactor.py: KFactor calibration endpoints
- calibration.py: Calibration Settings (owner-facing)
- ready_by.py: Ready-by scheduling endpoints
"""

# Import blueprint first (no dependencies)
# Import route modules to register routes on car_bp
from . import (
    bounded,
    calibration,
    comfort,
    control,
    departure,
    installation,
    kfactor,
    photos,
    ready_by,
    sessions,
    status,
    weather,
)
from ._blueprint import car_bp

__all__ = ["car_bp"]
