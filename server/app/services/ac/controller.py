import logging
import os
from typing import Any, ClassVar

import tinytuya

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("AC CONTROLLER: invalid %s=%r, using default=%s", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a bool environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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
        tuya_version: float | None = None,
        connection_timeout_s: float | None = None,
        persist: bool | None = None,
    ) -> None:
        """
        Initialize the controller.

        You can either pass an existing tinytuya `Device` instance via `tinytuya_device`
        OR supply the device id, ip and local key and this class will build the connection.
        """
        logger.debug(
            "AC CONTROLLER: __init__ winter=%s has_injected_device=%s ip=%s",
            winter,
            tinytuya_device is not None,
            IP or None,
        )
        if winter:
            logger.debug("AC CONTROLLER: skipping device initialization due to winter mode")
            return
        if tinytuya_device:
            self.ac = tinytuya_device
            logger.debug("AC CONTROLLER: using injected TinyTuya device instance")
        else:
            resolved_version = (
                float(tuya_version)
                if tuya_version is not None
                else _env_float("AC_TUYA_VERSION", 3.1)
            )
            resolved_timeout = (
                float(connection_timeout_s)
                if connection_timeout_s is not None
                else _env_float("AC_TUYA_TIMEOUT_S", 5.0)
            )
            if persist is None:
                resolved_persist = _env_bool("AC_TUYA_PERSIST", False)
            elif isinstance(persist, bool):
                resolved_persist = persist
            else:
                resolved_persist = str(persist).strip().lower() in {"1", "true", "yes", "on"}
            logger.debug(
                "AC CONTROLLER: creating TinyTuya device ip=%s version=%s timeout_s=%s persist=%s",
                IP or None,
                resolved_version,
                resolved_timeout,
                resolved_persist,
            )
            self.ac = tinytuya.Device(
                DEV_ID,
                IP,
                LOCALKEY,
                version=resolved_version,
                connection_timeout=resolved_timeout,
                persist=resolved_persist,
            )

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
        status_map: dict[str, Any] = {}

        try:
            logger.debug("AC CONTROLLER: get_status called")
            resp = self.ac.status()
            logger.debug("AC CONTROLLER: raw status response=%s", resp)
            if not isinstance(resp, dict):
                logger.warning(
                    "AC CONTROLLER: Unexpected status response type=%s value=%s",
                    type(resp).__name__,
                    resp,
                )
                return status_map

            if resp.get("Error"):
                logger.warning(
                    "AC CONTROLLER: status request failed err=%s error=%s payload=%s",
                    resp.get("Err"),
                    resp.get("Error"),
                    resp.get("Payload"),
                )
                return status_map

            result = resp.get("dps")
            if not isinstance(result, dict) or not result:
                logger.warning("AC CONTROLLER: empty status response dps=%s raw=%s", result, resp)
                return status_map

            status_map["switch"] = result.get(str(self.POWER))
            status_map["mode"] = result.get(str(self.MODE))
            status_map["fan_speed_enum"] = result.get(str(self.FAN))
            status_map["set_temperature"] = result.get(str(self.TEMP_SET))
            status_map["temp_current"] = result.get(str(self.TEMP_CURRENT))
            logger.debug("AC CONTROLLER: normalized status=%s", status_map)
            return status_map
        except Exception:
            logger.exception("AC CONTROLLER: error while fetching status")
            return status_map

    # -------------------------
    # Internals / validation
    # -------------------------

    def _send_commands(self, index: int, value: Any) -> dict[str, Any]:
        logger.debug("AC CONTROLLER: sending command index=%s value=%s", index, value)
        resp = self.ac.set_value(index, value)
        logger.debug("AC CONTROLLER: raw command response=%s", resp)
        if not isinstance(resp, dict):
            logger.warning(
                "AC CONTROLLER: Unexpected command response type=%s index=%s value=%s response=%s",
                type(resp).__name__,
                index,
                value,
                resp,
            )
            raise RuntimeError("AC command failed: unexpected device response")
        if resp.get("Error"):
            logger.warning(
                "AC CONTROLLER: command failed index=%s value=%s err=%s error=%s payload=%s",
                index,
                value,
                resp.get("Err"),
                resp.get("Error"),
                resp.get("Payload"),
            )
            err = resp.get("Err")
            detail = f"{resp.get('Error')} ({err})" if err else str(resp.get("Error"))
            raise RuntimeError(f"AC command failed: {detail}")
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
