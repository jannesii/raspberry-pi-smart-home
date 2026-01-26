import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .car_heater_service import CarHeaterService

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
        """
        with self._lock:
            self._settings = settings
        self._ctrl.save_keep_at_temp_settings(settings)
        logger.debug("Updated KeepAtTemp settings: %r", settings)

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
            if self._settings:
                self._settings.enabled = enabled
                self._ctrl.save_keep_at_temp_settings(self._settings)
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
