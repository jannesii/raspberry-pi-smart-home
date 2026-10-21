import logging
import math
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .car_heater_service import CarHeaterService

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TARGET_RANGE_C = (-50.0, 50.0)
HYSTERESIS_RANGE_C = (0.1, 10.0)


class ComfortUnavailable(ValueError):
    """Comfort cannot start: another heating mode owns the relay."""


@dataclass
class KeepAtTempSettings:
    target_temperature_c: float | None = None
    hysteresis_c: float | None = None
    enabled: bool | None = None


class KeepAtTempService:
    """
    Service to maintain car heater temperature.

    This is a placeholder for the actual implementation.
    """

    def __init__(self, car_heater_service: CarHeaterService, ctrl: "Controller") -> None:
        self._lock = threading.Lock()
        self._car_heater_service = car_heater_service
        self._ctrl = ctrl
        self._settings: KeepAtTempSettings | None = self._ctrl.get_keep_at_temp_settings()
        logger.info("KeepAtTempService initialized")

    def update_settings(self, settings: KeepAtTempSettings) -> None:
        """
        Update the keep-at-temperature settings.

        Persists before the in-memory copy changes, so a failed save never
        leaves the thermostat running on values that a reload would lose.
        """
        with self._lock:
            if settings.enabled:
                self._car_heater_service.require_mode_available()
            self._ctrl.save_keep_at_temp_settings(settings)
            self._settings = settings
        logger.debug("Updated KeepAtTemp settings: %r", settings)

    def apply_changes(self, changes: dict[str, Any]) -> KeepAtTempSettings:
        """Validate and persist a partial browser update; absent fields keep their values.

        Shared by the HTTP and Socket.IO paths. Raises ``ComfortUnavailable``
        when a start is refused by bounded-session ownership and ``ValueError``
        for invalid values; nothing is saved in either case.
        """
        unknown = set(changes) - {"enabled", "target_temperature_c", "hysteresis_c"}
        if unknown:
            raise ValueError(f"Unknown comfort field: {sorted(unknown)[0]}")
        with self._lock:
            current = self._settings or KeepAtTempSettings()
            settings = KeepAtTempSettings(
                target_temperature_c=current.target_temperature_c,
                hysteresis_c=current.hysteresis_c,
                enabled=bool(current.enabled),
            )
            if "enabled" in changes:
                if not isinstance(changes["enabled"], bool):
                    raise ValueError("enabled must be true or false")
                settings.enabled = changes["enabled"]
            if "target_temperature_c" in changes:
                settings.target_temperature_c = _bounded_number(
                    changes["target_temperature_c"], TARGET_RANGE_C, "Target temperature"
                )
            if "hysteresis_c" in changes:
                settings.hysteresis_c = _bounded_number(
                    changes["hysteresis_c"], HYSTERESIS_RANGE_C, "Hysteresis"
                )
            if settings.enabled and settings.target_temperature_c is None:
                raise ValueError("Save a target temperature before starting comfort heating")
            if settings.enabled and settings.hysteresis_c is None:
                raise ValueError(
                    "Save a hysteresis in Settings → Heating preferences before starting"
                )
            if settings.enabled and not current.enabled:
                reason = self.unavailable_reason()
                if reason:
                    logger.info("Comfort start refused: %s", reason)
                    raise ComfortUnavailable(reason)
            # The lock is held from the ownership check through the save, so a
            # bounded start's mode suspension (which takes it) runs afterwards.
            self._ctrl.save_keep_at_temp_settings(settings)
            self._settings = settings
            logger.info(
                "Comfort settings saved fields=%s enabled=%s",
                sorted(changes),
                settings.enabled,
            )
            return settings

    def unavailable_reason(self) -> str | None:
        """Why comfort cannot start now, or None when it can."""
        try:
            self._car_heater_service.require_mode_available()
        except ValueError:
            return "Stop heating and wait for confirmed OFF before starting comfort heating."
        return None

    def get_settings(self) -> KeepAtTempSettings | None:
        """
        Get the current keep-at-temperature settings.
        """
        with self._lock:
            return self._settings

    @property
    def enabled(self) -> bool:
        """
        Check if keep-at-temperature is enabled.
        """
        with self._lock:
            return self._settings.enabled if self._settings else False

    @enabled.setter
    def enabled(self, enabled: bool) -> None:
        """
        Enable or disable keep-at-temperature.
        """
        with self._lock:
            if enabled:
                self._car_heater_service.require_mode_available()
            if self._settings:
                settings = replace(self._settings, enabled=enabled)
                self._ctrl.save_keep_at_temp_settings(settings)
                self._settings = settings
                logger.debug("Set KeepAtTemp enabled to: %r", enabled)

    @property
    def target_temperature_c(self) -> float | None:
        """
        Get the target temperature in Celsius.
        """
        with self._lock:
            return self._settings.target_temperature_c if self._settings else None

    @target_temperature_c.setter
    def target_temperature_c(self, temp_c: float) -> None:
        """
        Set the target temperature in Celsius.
        """
        with self._lock:
            if self._settings:
                self._settings.target_temperature_c = temp_c
                self._ctrl.save_keep_at_temp_settings(self._settings)
                logger.debug("Set KeepAtTemp target_temperature_c to: %r", temp_c)

    def thermostat_logic(
        self,
        current_temp: float,
        heater_on: bool,
    ) -> None:
        """
        Logic to control the car heater based on temperature settings.
        """
        with self._lock:
            if self._settings is None or not self._settings.enabled:
                logger.debug("Keep-at-temp disabled or no settings.")
                return

            # Placeholder for actual temperature reading and control logic
            target_temp = self._settings.target_temperature_c
            hysteresis = self._settings.hysteresis_c

            if current_temp is None or target_temp is None or hysteresis is None:
                logger.debug("Insufficient data for thermostat logic.")
                return

            band: float = hysteresis / 2.0

            if not heater_on and current_temp < target_temp - band:
                self._turn_heater_on(current_temp, target_temp)
            elif heater_on and current_temp > target_temp + band:
                self._turn_heater_off(current_temp, target_temp)

    def tick(self, current_temp: float, heater_on: bool) -> None:
        """
        Background thread to manage keep-at-temperature logic.
        """
        if self._settings.enabled:
            self.thermostat_logic(current_temp, heater_on)

    def _turn_heater_on(self, current_temp: float, target_temp: float) -> None:
        """
        Send command to turn the car heater on.
        """
        self._car_heater_service.turn_on(
            source="keep_at_temp",
            reason=f"Temp {current_temp:.1f}°C below target {target_temp:.1f}°C",
        )
        logger.info("Heater ON via keep-at-temp: %.1f°C < %.1f°C", current_temp, target_temp)

    def _turn_heater_off(self, current_temp: float, target_temp: float) -> None:
        """
        Send command to turn the car heater off.
        """
        self._car_heater_service.turn_off(
            source="keep_at_temp",
            reason=f"Temp {current_temp:.1f}°C above target {target_temp:.1f}°C",
        )
        logger.info("Heater OFF via keep-at-temp: %.1f°C > %.1f°C", current_temp, target_temp)


def _bounded_number(value: Any, bounds: tuple[float, float], label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a number")
    if not bounds[0] <= value <= bounds[1]:
        raise ValueError(f"{label} must be between {bounds[0]:g} and {bounds[1]:g} °C")
    return float(value)
