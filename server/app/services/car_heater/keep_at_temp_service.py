import logging
import threading
from .car_heater_models import KeepAtTempSettings
from .car_heater_service import CarHeaterService
from ...core.controller import Controller

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class KeepAtTempService:
    """
    Service to maintain car heater temperature.

    This is a placeholder for the actual implementation.
    """

    def __init__(self, car_heater_service: CarHeaterService, ctrl: Controller) -> None:
        self._lock = threading.Lock()
        self._car_heater_service = car_heater_service
        self._ctrl: Controller = ctrl
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
    def is_enabled(self) -> bool:
        """
        Check if keep-at-temperature is enabled.
        """
        with self._lock:
            return self._settings.enabled if self._settings else False
        
    @is_enabled.setter
    def is_enabled(self, enabled: bool) -> None:
        """
        Enable or disable keep-at-temperature.
        """
        with self._lock:
            if self._settings:
                self._settings.enabled = enabled
                self._ctrl.save_keep_at_temp_settings(self._settings)
                logger.debug("Set KeepAtTemp enabled to: %r", enabled)

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
                self._turn_heater_on()
            elif heater_on and current_temp > target_temp + band:
                self._turn_heater_off()

    def tick(self, current_temp: float, heater_on: bool) -> None:
        """
        Background thread to manage keep-at-temperature logic.
        """
        if self._settings.enabled:
            self.thermostat_logic(current_temp, heater_on)

    def _turn_heater_on(self) -> None:
        """
        Send command to turn the car heater on.
        """
        command = {"action": "turn_on"}
        self._car_heater_service.queue_command(command)
        logger.info("Queued command to turn car heater ON")

    def _turn_heater_off(self) -> None:
        """
        Send command to turn the car heater off.
        """
        command = {"action": "turn_off"}
        self._car_heater_service.queue_command(command)
        logger.info("Queued command to turn car heater OFF")
