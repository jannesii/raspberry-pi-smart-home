"""
Notification emitters for thermostat.

Handles emitting status updates to listeners via callback.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class NotificationEmitter:
    """Emits status notifications to listeners."""

    def __init__(
        self,
        notify: Callable[[str, dict[str, Any]], None] | None,
        cfg: Any,
    ) -> None:
        self._notify = notify
        self._cfg = cfg

    def emit_status(self, is_on: bool) -> None:
        """Notify listeners about current AC on/off state."""
        try:
            if self._notify:
                self._notify("ac_status", {"is_on": bool(is_on)})
        except Exception as e:
            logger.debug("thermo: notify failed: %s", e)

    def emit_thermostat_status(self, enabled: bool) -> None:
        """Notify listeners about thermostat enabled state."""
        try:
            if self._notify:
                self._notify(
                    "thermostat_status",
                    {
                        "enabled": bool(enabled),
                        "thermo_active": bool(enabled),
                    },
                )
        except Exception as e:
            logger.debug("thermo: notify thermo failed: %s", e)

    def emit_sleep_status(self, payload: dict[str, Any]) -> None:
        """Notify listeners about sleep status."""
        try:
            if self._notify:
                self._notify("sleep_status", payload)
        except Exception as e:
            logger.debug("thermo: notify sleep failed: %s", e)

    def emit_config(self) -> None:
        """Notify listeners about thermostat config."""
        logger.debug("emit_config called")
        try:
            if self._notify:
                payload: dict[str, Any] = {
                    "setpoint_c": float(self._cfg.target_temp),
                    "pos_hysteresis": float(self._cfg.pos_hysteresis),
                    "neg_hysteresis": float(self._cfg.neg_hysteresis),
                    "min_on_s": int(self._cfg.min_on_s),
                    "min_off_s": int(self._cfg.min_off_s),
                    "poll_interval_s": int(self._cfg.poll_interval_s),
                    "smooth_window": int(self._cfg.smooth_window),
                    "max_stale_s": None
                    if self._cfg.max_stale_s is None
                    else int(self._cfg.max_stale_s),
                }
                with contextlib.suppress(Exception):
                    payload["control_locations"] = getattr(self._cfg, "control_locations", None)
                self._notify("thermo_config", payload)
        except Exception as e:
            logger.debug("thermo: notify config failed: %s", e)

    def emit_ac_state(self, mode: str | None, fan_speed: str | None) -> None:
        """Notify listeners about AC mode and fan speed."""
        try:
            if self._notify:
                self._notify("ac_state", {"mode": mode, "fan_speed": fan_speed})
        except Exception as e:
            logger.debug("thermo: notify ac_state failed: %s", e)
