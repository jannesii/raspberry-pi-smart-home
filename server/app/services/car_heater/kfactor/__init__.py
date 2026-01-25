"""
KFactor Calibration Module.

Auto-calibrates thermal parameters (k_loss, eta) for cabin heating prediction.
"""
from .calibrator import KFactorCalibrator
from .constants import (
    CABIN_VOLUME_M3,
    HEAT_CAPACITY_J_PER_K,
    HEATER_POWER_W,
    AIR_DENSITY_KG_M3,
    SPECIFIC_HEAT_J_KG_K,
)
from .models import KFactorConfig, KFactorFit, KFactorLastSession, KFactorSample
from .physics import ThermalPhysics
from .session import SessionManager
from .snapshot import SnapshotGenerator

__all__ = [
    # Main class
    "KFactorCalibrator",
    # Models
    "KFactorConfig",
    "KFactorFit",
    "KFactorLastSession",
    "KFactorSample",
    # Physics
    "ThermalPhysics",
    # Session
    "SessionManager",
    # Snapshot
    "SnapshotGenerator",
    # Constants
    "CABIN_VOLUME_M3",
    "HEAT_CAPACITY_J_PER_K",
    "HEATER_POWER_W",
    "AIR_DENSITY_KG_M3",
    "SPECIFIC_HEAT_J_KG_K",
]
