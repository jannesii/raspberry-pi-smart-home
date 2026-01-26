import os
import time
import random
import logging
from typing import Optional, Union, Any, Dict

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
#  Platform‑specific imports
# ----------------------------------------------------------------------
ON_WINDOWS = os.name == "nt"

if not ON_WINDOWS:
    import board  # type: ignore
    import adafruit_dht  # type: ignore[reportMissingImports]
else:
    # Stubs so that type checkers don't complain if you reference them.
    board = adafruit_dht = None  # type: ignore


# ----------------------------------------------------------------------
#  Helper: resolve "pin" argument on Pi
# ----------------------------------------------------------------------
def _resolve_board_pin(pin: Union[int, Any]) -> Any:
    """
    Accepts either a board pin object or an **integer BCM pin number** and
    returns the actual `board.Dxx` constant (Pi only).

    Raises ValueError on invalid BCM numbers.
    """
    if not isinstance(pin, int):  # already a board pin object
        return pin

    pin_name = f"D{pin}"
    try:
        return getattr(board, pin_name)
    except AttributeError:
        raise ValueError(f"Invalid BCM pin number: {pin}") from None


# ----------------------------------------------------------------------
#  Main class
# ----------------------------------------------------------------------
class DHT22Sensor:
    """
    Cross‑platform DHT22 helper.

    * Raspberry Pi  : uses CircuitPython `adafruit_dht` (real hardware)
    * Windows       : **simulated** temperature & humidity values

    Example
    -------
    >>> sensor = DHT22Sensor(pin=4)      # BCM4 on Pi, anything on Win
    >>> data   = sensor.read()
    >>> print(data["temperature"], data["humidity"])
    """

    def __init__(
        self,
        pin: Union[int, Any],
        retries: int = 3,
        delay: float = 2.0,
        simulated_base: tuple[float, float] = (24.0, 50.0),
    ):
        """
        Parameters
        ----------
        pin : int | board pin
            • On Raspberry Pi:  BCM pin number **or** a `board.<PIN>` object.
            • On Windows:      ignored (kept for API compatibility).

        retries : int
            Maximum number of attempts per `read()` call.

        delay : float
            Seconds to wait between retries.

        simulated_base : (float, float)
            Starting (temp, hum) for the Windows simulator.  Small random
            drift (±0.5 °C / ±1 %) is added on each read.
        """
        self._is_simulated = ON_WINDOWS
        self.retries = retries
        self.delay = delay

        # Runtime attributes exposed via getters
        self.temperature: Optional[float] = None
        self.humidity: Optional[float] = None

        if self._is_simulated:
            # Initialise the simulator
            self._sim_temp, self._sim_hum = simulated_base
            logger.info("running in **simulation** mode")
        else:
            # Real hardware path
            resolved_pin = _resolve_board_pin(pin)
            self.dht = adafruit_dht.DHT22(resolved_pin, use_pulseio=False)
            logger.info("initialised on pin %s", resolved_pin)

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def read(self) -> Dict[str, float]:
        """
        Try up to `retries` times and return the last successful read.

        Returns
        -------
        dict
            {'temperature': °C, 'humidity': %RH}

        Raises
        ------
        RuntimeError
            If *all* attempts fail (real hardware path) or the simulator
            encounters an unexpected error.
        """
        if self._is_simulated:
            return self._simulated_read()

        for attempt in range(1, self.retries + 1):
            try:
                temp = self.dht.temperature
                hum = self.dht.humidity
                if temp is None or hum is None:
                    raise RuntimeError("no data")
                self.temperature, self.humidity = temp, hum
                logger.debug("read OK %.1f °C, %.1f %%", temp, hum)
                return {"temperature": temp, "humidity": hum}

            except RuntimeError as exc:
                logger.warning(
                    "attempt %s failed (%s), retrying in %.1fs",
                    attempt,
                    exc,
                    self.delay,
                )
                time.sleep(self.delay)

        # every attempt failed
        msg = f"failed after {self.retries} attempts"
        logger.error(msg)
        raise RuntimeError(msg)

    def get_temperature(self) -> float:
        """Return the last temperature reading (°C)."""
        if self.temperature is None:
            raise RuntimeError("No temperature yet; call read() first.")
        return self.temperature

    def get_humidity(self) -> float:
        """Return the last humidity reading (%RH)."""
        if self.humidity is None:
            raise RuntimeError("No humidity yet; call read() first.")
        return self.humidity

    # ------------------------------------------------------------------
    #  Private helpers
    # ------------------------------------------------------------------
    def _simulated_read(self) -> Dict[str, float]:
        """
        Produce pseudo‑random but realistic readings on Windows.
        """
        # simple bounded random walk
        self._sim_temp += random.uniform(-0.5, 0.5)
        self._sim_hum += random.uniform(-1.0, 1.0)

        # Clamp to plausible ranges
        self._sim_temp = max(-10.0, min(self._sim_temp, 40.0))
        self._sim_hum = max(0.0, min(self._sim_hum, 100.0))

        self.temperature = round(self._sim_temp, 1)
        self.humidity = round(self._sim_hum, 1)

        logger.debug("%.1f °C, %.1f %%", self.temperature, self.humidity)
        return {"temperature": self.temperature, "humidity": self.humidity}
