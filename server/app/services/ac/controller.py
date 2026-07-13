import logging
import os
import threading
import time
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


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable with a safe default."""
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
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

    TRANSIENT_RESPONSE_ERRORS: ClassVar[frozenset[str]] = frozenset({"900", "904"})
    DEFAULT_RETRY_ATTEMPTS = 3
    DEFAULT_RETRY_DELAY_S = 0.25
    PARTIAL_POWER_WARNING_INTERVAL_S = 300.0

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
        retry_attempts: int | None = None,
        retry_delay_s: float | None = None,
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
        resolved_retry_attempts = (
            int(retry_attempts)
            if retry_attempts is not None
            else _env_int("AC_TUYA_RETRY_ATTEMPTS", self.DEFAULT_RETRY_ATTEMPTS)
        )
        resolved_retry_delay_s = (
            float(retry_delay_s)
            if retry_delay_s is not None
            else _env_float("AC_TUYA_RETRY_DELAY_S", self.DEFAULT_RETRY_DELAY_S)
        )
        self._retry_attempts = max(1, resolved_retry_attempts)
        self._retry_delay_s = max(0.0, resolved_retry_delay_s)
        self._device_lock = threading.RLock()
        self._last_call_diagnostics: dict[str, Any] = {}
        self._last_partial_power_warning_at = float("-inf")
        logger.debug(
            "AC CONTROLLER: transient retry attempts=%s delay_s=%s",
            self._retry_attempts,
            self._retry_delay_s,
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

    def turn_on(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        logger.debug(
            "AC CONTROLLER: control turn_on requested correlation_id=%s",
            correlation_id,
        )
        return self._send_commands(self.POWER, True, correlation_id=correlation_id)

    def turn_off(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        logger.debug(
            "AC CONTROLLER: control turn_off requested correlation_id=%s",
            correlation_id,
        )
        return self._send_commands(self.POWER, False, correlation_id=correlation_id)

    def set_mode(self, mode: str) -> dict[str, Any]:
        mode_l = mode.strip().lower()
        self._validate_mode(mode_l)
        logger.debug("AC CONTROLLER: control set_mode requested mode=%s", mode_l)
        return self._send_commands(self.MODE, mode_l)

    def set_fan_speed(self, speed: str) -> dict[str, Any]:
        speed_l = speed.strip().lower()
        self._validate_fan_speed(speed_l)
        logger.debug("AC CONTROLLER: control set_fan_speed requested speed=%s", speed_l)
        return self._send_commands(self.FAN, speed_l)

    def set_temperature(self, celsius: int) -> dict[str, Any]:
        self._validate_temperature(celsius)
        logger.debug("AC CONTROLLER: control set_temperature requested celsius=%s", celsius)
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
        status_map, _diagnostics = self.get_status_with_diagnostics()
        return status_map

    def get_status_with_diagnostics(
        self,
        *,
        correlation_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return normalized status together with safe transport diagnostics."""
        status_map: dict[str, Any] = {}

        try:
            logger.debug(
                "AC CONTROLLER: get_status called correlation_id=%s",
                correlation_id,
            )
            with self._device_lock:
                resp = self._call_device_with_retries(
                    "status",
                    correlation_id=correlation_id,
                )
                diagnostics = dict(self._last_call_diagnostics)
                logger.debug("AC CONTROLLER: raw status response=%s", resp)
                if not isinstance(resp, dict):
                    logger.warning(
                        "AC CONTROLLER: Unexpected status response type=%s value=%s",
                        type(resp).__name__,
                        resp,
                    )
                    return status_map, diagnostics

                if resp.get("Error"):
                    logger.warning(
                        "AC CONTROLLER: status request failed err=%s error=%s payload=%s",
                        resp.get("Err"),
                        resp.get("Error"),
                        resp.get("Payload"),
                    )
                    return status_map, diagnostics

                result = resp.get("dps")
                if not isinstance(result, dict) or not result:
                    logger.warning(
                        "AC CONTROLLER: empty status response dps=%s raw=%s",
                        result,
                        resp,
                    )
                    return status_map, diagnostics

                field_map = (
                    ("switch", self.POWER),
                    ("mode", self.MODE),
                    ("fan_speed_enum", self.FAN),
                    ("set_temperature", self.TEMP_SET),
                    ("temp_current", self.TEMP_CURRENT),
                )
                missing_dps: list[str] = []
                for field_name, dp_code in field_map:
                    dp_key = str(dp_code)
                    if dp_key not in result or result[dp_key] is None:
                        missing_dps.append(dp_key)
                        continue
                    status_map[field_name] = result[dp_key]

                if missing_dps:
                    logger.debug(
                        "AC CONTROLLER: partial status response missing_dps=%s present_dps=%s",
                        missing_dps,
                        sorted(result),
                    )
                    if str(self.POWER) in missing_dps:
                        self._log_missing_power_dps(
                            present_dps=sorted(result),
                            diagnostics=diagnostics,
                        )
                logger.debug("AC CONTROLLER: normalized status=%s", status_map)
                return status_map, diagnostics
        except Exception:
            logger.exception("AC CONTROLLER: error while fetching status")
            return status_map, dict(self._last_call_diagnostics)

    # -------------------------
    # Internals / validation
    # -------------------------

    def _send_commands(
        self,
        index: int,
        value: Any,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        logger.debug(
            "AC CONTROLLER: sending command index=%s value=%s correlation_id=%s",
            index,
            value,
            correlation_id,
        )
        with self._device_lock:
            resp = self._call_device_with_retries(
                "set_value",
                index,
                value,
                correlation_id=correlation_id,
            )
            diagnostics = dict(self._last_call_diagnostics)
        logger.debug("AC CONTROLLER: raw command response=%s", resp)
        if index == self.POWER:
            logger.info(
                "AC CONTROLLER: power command response correlation_id=%s expected_on=%s "
                "response_power=%r response_power_type=%s error=%s sent_seq=%s "
                "sent_cmd=%s received_seq=%s received_cmd=%s sequence_match=%s "
                "command_match=%s persistent=%s attempt=%s",
                correlation_id,
                bool(value),
                diagnostics.get("response_power"),
                diagnostics.get("response_power_type"),
                diagnostics.get("response_error"),
                diagnostics.get("sent_seq"),
                diagnostics.get("sent_cmd"),
                diagnostics.get("received_seq"),
                diagnostics.get("received_cmd"),
                diagnostics.get("sequence_match"),
                diagnostics.get("command_match"),
                diagnostics.get("persistent"),
                diagnostics.get("attempt"),
            )
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

    def _call_device_with_retries(
        self,
        method_name: str,
        *args: Any,
        correlation_id: str | None = None,
    ) -> Any:
        """Serialize TinyTuya access and retry transient malformed responses."""
        with self._device_lock:
            for attempt in range(1, self._retry_attempts + 1):
                logger.debug(
                    "AC CONTROLLER: device call method=%s attempt=%s/%s",
                    method_name,
                    attempt,
                    self._retry_attempts,
                )
                response = getattr(self.ac, method_name)(*args)
                self._last_call_diagnostics = self._build_call_diagnostics(
                    method_name=method_name,
                    response=response,
                    attempt=attempt,
                    correlation_id=correlation_id,
                )
                if not self._is_transient_response(response):
                    if attempt > 1:
                        logger.debug(
                            "AC CONTROLLER: device call recovered method=%s attempt=%s",
                            method_name,
                            attempt,
                        )
                    return response

                if attempt >= self._retry_attempts:
                    return response

                logger.debug(
                    "AC CONTROLLER: transient device response method=%s err=%s "
                    "attempt=%s/%s; resetting connection",
                    method_name,
                    response.get("Err"),
                    attempt,
                    self._retry_attempts,
                )
                self._reset_device_connection()
                if self._retry_delay_s:
                    time.sleep(self._retry_delay_s)

        raise RuntimeError("AC device retry loop exited unexpectedly")

    def _build_call_diagnostics(
        self,
        *,
        method_name: str,
        response: Any,
        attempt: int,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        """Build a credential-free summary of one TinyTuya response."""
        sent = getattr(self.ac, "raw_sent", None)
        received_messages = getattr(self.ac, "raw_recv", None)
        received = (
            received_messages[-1]
            if isinstance(received_messages, list) and received_messages
            else None
        )
        sent_seq = getattr(sent, "seqno", None)
        sent_cmd = getattr(sent, "cmd", None)
        received_seq = getattr(received, "seqno", None)
        received_cmd = getattr(received, "cmd", None)
        result = response.get("dps") if isinstance(response, dict) else None
        response_power = result.get(str(self.POWER)) if isinstance(result, dict) else None
        diagnostics = {
            "method": method_name,
            "attempt": attempt,
            "correlation_id": correlation_id,
            "persistent": bool(getattr(self.ac, "socketPersistent", False)),
            "sent_seq": sent_seq,
            "sent_cmd": sent_cmd,
            "received_seq": received_seq,
            "received_cmd": received_cmd,
            "sequence_match": (
                sent_seq == received_seq
                if sent_seq is not None and received_seq is not None
                else None
            ),
            "command_match": (
                sent_cmd == received_cmd
                if sent_cmd is not None and received_cmd is not None
                else None
            ),
            "response_power": response_power,
            "response_power_type": (
                type(response_power).__name__ if response_power is not None else None
            ),
            "response_error": response.get("Err") if isinstance(response, dict) else None,
        }
        logger.debug("AC CONTROLLER: call diagnostics=%s", diagnostics)
        return diagnostics

    def _log_missing_power_dps(
        self,
        *,
        present_dps: list[str],
        diagnostics: dict[str, Any],
    ) -> None:
        """Rate-limit production warnings for partial payloads without DPS 1."""
        now = time.monotonic()
        elapsed = now - self._last_partial_power_warning_at
        if elapsed < self.PARTIAL_POWER_WARNING_INTERVAL_S:
            logger.debug(
                "AC CONTROLLER: suppressing repeated missing power DPS warning " "elapsed_s=%.1f",
                elapsed,
            )
            return
        self._last_partial_power_warning_at = now
        logger.warning(
            "AC CONTROLLER: partial status missing power DPS correlation_id=%s "
            "present_dps=%s sent_seq=%s sent_cmd=%s received_seq=%s "
            "received_cmd=%s sequence_match=%s command_match=%s persistent=%s",
            diagnostics.get("correlation_id"),
            present_dps,
            diagnostics.get("sent_seq"),
            diagnostics.get("sent_cmd"),
            diagnostics.get("received_seq"),
            diagnostics.get("received_cmd"),
            diagnostics.get("sequence_match"),
            diagnostics.get("command_match"),
            diagnostics.get("persistent"),
        )

    def _is_transient_response(self, response: Any) -> bool:
        """Return whether a TinyTuya response is safe to retry."""
        if not isinstance(response, dict) or not response.get("Error"):
            return False
        return str(response.get("Err") or "") in self.TRANSIENT_RESPONSE_ERRORS

    def _reset_device_connection(self) -> None:
        """Close the current TinyTuya socket so the next attempt reconnects."""
        close = getattr(self.ac, "close", None)
        if not callable(close):
            logger.debug("AC CONTROLLER: device has no close method for retry reset")
            return
        try:
            close()
        except Exception:
            logger.debug("AC CONTROLLER: failed to reset device connection", exc_info=True)

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
