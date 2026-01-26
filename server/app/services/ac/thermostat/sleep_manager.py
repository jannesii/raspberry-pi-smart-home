"""
Sleep window management for thermostat.

Handles sleep time window checking with weekly schedule support.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from .time_utils import epoch_to_hhmm, now_minutes_local, parse_hhmm_to_minutes

logger = logging.getLogger(__name__)


class SleepManager:
    """Manages sleep window state and configuration."""

    def __init__(self, cfg: Any, tz: Any) -> None:
        self._cfg = cfg
        self._tz = tz
        self._is_sleep_time: bool = False
        self._sleep_override_until: float | None = None

    @property
    def is_sleep_time(self) -> bool:
        return self._is_sleep_time

    @is_sleep_time.setter
    def is_sleep_time(self, value: bool) -> None:
        self._is_sleep_time = value

    @property
    def override_until(self) -> float | None:
        return self._sleep_override_until

    def is_sleep_window_now(self) -> bool:
        """Return True if current local time falls within configured sleep window.

        Supports optional weekly schedule; falls back to single start/stop.
        """
        if not getattr(self._cfg, "sleep_active", True):
            return False

        # Honor temporary override: when active, pretend not in sleep window
        if self._sleep_override_until is not None:
            now = time.time()
            if now < float(self._sleep_override_until):
                return False
            # Expired -> clear override
            self._sleep_override_until = None

        # Try weekly schedule first
        try:
            weekly = getattr(self._cfg, "sleep_weekly", None)
            if weekly:
                schedule = json.loads(weekly) if isinstance(weekly, str) else weekly
                # Map weekday 0=Mon..6=Sun
                wday = time.localtime().tm_wday
                keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                key = keys[wday] if 0 <= wday < len(keys) else None

                if key and isinstance(schedule, dict) and key in schedule:
                    day = schedule.get(key) or {}
                    start = (day.get("start") or "").strip() or None
                    stop = (day.get("stop") or "").strip() or None

                    start_m = parse_hhmm_to_minutes(start)
                    stop_m = parse_hhmm_to_minutes(stop)

                    if start_m is None or stop_m is None:
                        return False

                    now_m = now_minutes_local()
                    if start_m == stop_m:
                        return False
                    if start_m < stop_m:
                        return start_m <= now_m < stop_m
                    # Wraps past midnight
                    return (now_m >= start_m) or (now_m < stop_m)
        except Exception as e:
            logger.debug("thermo: failed weekly sleep parse: %s", e)

        # Fallback: single start/stop
        start_m = parse_hhmm_to_minutes(self._cfg.sleep_start)
        stop_m = parse_hhmm_to_minutes(self._cfg.sleep_stop)

        if start_m is None or stop_m is None:
            return False

        now_m = now_minutes_local()
        if start_m == stop_m:
            logger.debug("thermo: sleep window start==stop; ignoring sleep")
            return False

        if start_m < stop_m:
            in_sleep = start_m <= now_m < stop_m
        else:
            # Wraps past midnight
            in_sleep = (now_m >= start_m) or (now_m < stop_m)

        logger.debug(
            "thermo: sleep_check now=%02d:%02d start=%s stop=%s -> %s",
            now_m // 60,
            now_m % 60,
            self._cfg.sleep_start,
            self._cfg.sleep_stop,
            in_sleep,
        )
        return in_sleep

    def set_enabled(self, enabled: bool) -> None:
        """Set sleep mode enabled state."""
        self._cfg.sleep_active = bool(enabled)

    def set_times(self, start: str | None, stop: str | None) -> None:
        """Set single sleep start/stop times."""
        self._cfg.sleep_start = start
        self._cfg.sleep_stop = stop

    def set_schedule(self, schedule: dict[str, dict[str, str | None]]) -> None:
        """Set weekly sleep schedule from dict mapping days to {start, stop}."""
        try:
            keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            norm: dict[str, dict[str, str | None]] = {}
            for k in keys:
                d = schedule.get(k) if isinstance(schedule, dict) else None
                if isinstance(d, dict):
                    s = d.get("start")
                    e = d.get("stop")
                    norm[k] = {
                        "start": s if isinstance(s, str) and ":" in s else None,
                        "stop": e if isinstance(e, str) and ":" in e else None,
                    }
            self._cfg.sleep_weekly = json.dumps(norm)
        except Exception as e:
            logger.debug("thermo: set_sleep_schedule failed: %s", e)

    def disable_for(self, minutes: int) -> None:
        """Temporarily disable sleep enforcement for the given minutes."""
        try:
            m = int(minutes)
        except Exception:
            return
        if m <= 0:
            return
        self._sleep_override_until = time.time() + (m * 60)
        logger.info(
            "thermo: sleep override enabled for %d minutes (until %s)",
            m,
            (datetime.now() + timedelta(minutes=m)).strftime("%H:%M"),
        )

    def get_status_payload(self) -> dict[str, Any]:
        """Build sleep status payload for notification."""
        payload: dict[str, Any] = {
            "sleep_enabled": bool(getattr(self._cfg, "sleep_active", True)),
            "sleep_start": getattr(self._cfg, "sleep_start", None),
            "sleep_stop": getattr(self._cfg, "sleep_stop", None),
            "sleep_time_active": bool(self.is_sleep_window_now()),
        }

        # Attach weekly schedule (as dict) if present
        weekly = getattr(self._cfg, "sleep_weekly", None)
        if weekly:
            try:
                payload["sleep_schedule"] = (
                    json.loads(weekly) if isinstance(weekly, str) else weekly
                )
            except Exception:
                payload["sleep_schedule"] = None

        # Attach temporary override info if active
        if self._sleep_override_until is not None:
            payload["sleep_override_until"] = epoch_to_hhmm(
                float(self._sleep_override_until), self._tz
            )

        return payload
