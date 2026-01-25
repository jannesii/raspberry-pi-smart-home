"""
AC Thermostat Module.

Temperature-based AC control with sleep scheduling and hysteresis.
"""
from ._thermostat import ACThermostat
from .sleep_manager import SleepManager
from .temp_reader import TemperatureReader
from .notifier import NotificationEmitter
from .time_utils import (
    parse_iso_to_epoch,
    parse_hhmm_to_minutes,
    epoch_to_hhmm,
    now_minutes_local,
    compute_phase_duration,
)

__all__ = [
    # Main class
    "ACThermostat",
    # Submodules
    "SleepManager",
    "TemperatureReader",
    "NotificationEmitter",
    # Time utilities
    "parse_iso_to_epoch",
    "parse_hhmm_to_minutes",
    "epoch_to_hhmm",
    "now_minutes_local",
    "compute_phase_duration",
]
