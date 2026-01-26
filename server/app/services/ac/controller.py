import logging
from typing import Any, ClassVar

import tinytuya

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ACController:
    """
    Controller for a Tuya/Smart Life AC using a tinytuya local device connection.

    Supported DP codes (from your device):
      - switch (bool)                     -> power on/off
      - mode (enum)                       -> 'cold' | 'wet' | 'wind'
      - fan_speed_enum (enum)             -> 'low' | 'high'
      - temp_set (int, 16..31, °C)        -> target temperature
      - temp_current (int, -20..100, °C)  -> reported current temperature (read-only)

    Usage:
        device = tinytuya.Device(DEV_ID, IP, LOCALKEY)

        ac = ACController(tinytuya_device=device)
        ac.turn_on()
        ac.set_mode("cold")
        ac.set_fan_speed("high")
        ac.set_temperature(23)
        print(ac.get_status())
    """

    # Enumerations and ranges from your provided specs
    FAN_SPEEDS: ClassVar[set[str]] = {"low", "high"}
    MODES: ClassVar[set[str]] = {"cold", "wet", "wind"}
    TEMP_MIN = 16
    TEMP_MAX = 31

    POWER = 1
    TEMP_SET = 2
    TEMP_CURRENT = 3
    MODE = 4
    FAN = 5

    def __init__(
        self,
        tinytuya_device: tinytuya.Device | None = None,
        # tinytuya device credentials (if not passing an existing Device instance)
        DEV_ID: str = "",
        IP: str = "",
        LOCALKEY: str = "",
        winter: bool = False,
    ) -> None:
        """
        Initialize the controller.

        You can either pass an existing tinytuya `Device` instance via `tinytuya_device`
        OR supply the device id, ip and local key and this class will build the connection.
        """
        if winter:
            return
        if tinytuya_device:
            self.ac = tinytuya_device
        else:
            self.ac = tinytuya.Device(DEV_ID, IP, LOCALKEY)

    # -------------------------
    # Public control operations
    # -------------------------

    def turn_on(self) -> dict[str, Any]:
        return self._send_commands(self.POWER, True)

    def turn_off(self) -> dict[str, Any]:
        return self._send_commands(self.POWER, False)

    def set_mode(self, mode: str) -> dict[str, Any]:
        mode_l = mode.strip().lower()
        self._validate_mode(mode_l)
        return self._send_commands(self.MODE, mode_l)

    def set_fan_speed(self, speed: str) -> dict[str, Any]:
        speed_l = speed.strip().lower()
        self._validate_fan_speed(speed_l)
        return self._send_commands(self.FAN, speed_l)

    def set_temperature(self, celsius: int) -> dict[str, Any]:
        self._validate_temperature(celsius)
        return self._send_commands(self.TEMP_SET, celsius)

    def get_status(self) -> dict[str, Any]:
        """
        Returns a dict keyed by DP code:
          {
            "switch": True/False,
            "mode": "cold"|"wet"|"wind",
            "fan_speed_enum": "low"|"high",
            "temp_set": int,
            "temp_current": int,
            ... (other codes if present)
          }
        """
        resp = self.ac.status()
        result = resp.get("dps", [])
        status_map: dict[str, Any] = {}

        try:
            if result:
                logger.debug("AC CONTROLLER: Status response: %s", result)
                status_map["switch"] = result.get(str(self.POWER))
                status_map["mode"] = result.get(str(self.MODE))
                status_map["fan_speed_enum"] = result.get(str(self.FAN))
                status_map["set_temperature"] = result.get(str(self.TEMP_SET))
            else:
                logger.warning("AC CONTROLLER: Empty status response:", result)

            return status_map
        except Exception as e:
            logger.exception("AC CONTROLLER: Error while fetching status:", e)

    # -------------------------
    # Internals / validation
    # -------------------------

    def _send_commands(self, index: int, value: Any) -> dict[str, Any]:
        resp = self.ac.set_value(index, value)
        return resp

    def _validate_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode '{mode}'. Allowed: {sorted(self.MODES)}")

    def _validate_fan_speed(self, speed: str) -> None:
        if speed not in self.FAN_SPEEDS:
            raise ValueError(f"Invalid fan speed '{speed}'. Allowed: {sorted(self.FAN_SPEEDS)}")

    def _validate_temperature(self, celsius: int) -> None:
        if not (self.TEMP_MIN <= celsius <= self.TEMP_MAX):
            raise ValueError(
                f"Invalid temp_set {celsius}. Range: {self.TEMP_MIN}..{self.TEMP_MAX} °C"
            )
