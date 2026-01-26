"""
AC Thermostat Module.

Temperature-based AC control with sleep scheduling and hysteresis.
"""

from ._thermostat import ACThermostat
from .notifier import NotificationEmitter
from .sleep_manager import SleepManager
from .temp_reader import TemperatureReader
from .time_utils import (
    compute_phase_duration,
    epoch_to_hhmm,
    now_minutes_local,
    parse_hhmm_to_minutes,
    parse_iso_to_epoch,
)

__all__ = [
    "ACThermostat",
    "NotificationEmitter",
    "SleepManager",
    "TemperatureReader",
    "compute_phase_duration",
    "epoch_to_hhmm",
    "now_minutes_local",
    "parse_hhmm_to_minutes",
    "parse_iso_to_epoch",
]
