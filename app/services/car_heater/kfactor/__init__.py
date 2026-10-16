"""
KFactor Calibration Module.

Auto-calibrates thermal parameters (k_loss, eta) for cabin heating prediction.
"""

from .calibrator import KFactorCalibrator
from .constants import (
    AIR_DENSITY_KG_M3,
    CABIN_VOLUME_M3,
    HEAT_CAPACITY_J_PER_K,
    HEATER_POWER_W,
    SPECIFIC_HEAT_J_KG_K,
)
from .models import KFactorConfig, KFactorFit, KFactorLastSession, KFactorSample
from .physics import ThermalPhysics
from .session import SessionManager
from .snapshot import SnapshotGenerator

__all__ = [
    "AIR_DENSITY_KG_M3",
    "CABIN_VOLUME_M3",
    "HEATER_POWER_W",
    "HEAT_CAPACITY_J_PER_K",
    "SPECIFIC_HEAT_J_KG_K",
    "KFactorCalibrator",
    "KFactorConfig",
    "KFactorFit",
    "KFactorLastSession",
    "KFactorSample",
    "SessionManager",
    "SnapshotGenerator",
    "ThermalPhysics",
]
