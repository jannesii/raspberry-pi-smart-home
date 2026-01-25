"""
Temperature reading for thermostat.

Handles reading from multiple control locations with staleness checking and smoothing.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core import Controller

logger = logging.getLogger(__name__)


class TemperatureReader:
    """Reads and smooths temperature from configured control locations."""

    def __init__(
        self,
        ctrl: "Controller",
        cfg: Any,
        location: str,
        tz: Any,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = cfg
        self._location = location
        self._tz = tz
        self._temps: deque[float] = deque(maxlen=max(1, cfg.smooth_window))

    def _now(self) -> float:
        return time.time()

    def _parse_iso_to_epoch(self, ts: str | None) -> float | None:
        """Parse ISO timestamp to epoch seconds."""
        if not ts:
            return None
        s = str(ts).strip()
        try:
            if s.endswith('Z'):
                s = s[:-1]
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz)
            return dt.timestamp()
        except Exception:
            try:
                return float(s)
            except Exception:
                return None

    def _get_control_locations(self) -> list[str]:
        """Get list of control locations from config."""
        locs: list[str] = []
        try:
            sel = getattr(self._cfg, 'control_locations', None)
            if sel:
                if isinstance(sel, str):
                    locs = [str(x)
                            for x in json.loads(sel) if isinstance(x, str)]
                elif isinstance(sel, (list, tuple)):
                    locs = [str(x) for x in sel if isinstance(x, str)]
        except Exception:
            locs = []
        if not locs:
            locs = [self._location]
        return locs

    def read_temperature(self) -> float | None:
        """Read latest temperature from selected control locations and apply smoothing.

        If multiple locations are selected, uses their average.
        Returns smoothed temperature or None if no valid readings.
        """
        locs = self._get_control_locations()
        temps: list[float] = []
        used_locs: list[str] = []
        latest_ts: str | None = None
        max_stale = self._cfg.max_stale_s

        for loc in locs:
            rec = self._ctrl.get_last_esp32_temphum_for_location(loc)
            if rec is None or rec.temperature is None:
                continue

            ts_epoch = self._parse_iso_to_epoch(
                getattr(rec, 'timestamp', None))
            if max_stale is not None and ts_epoch is not None:
                age = self._now() - ts_epoch
                if age > max_stale:
                    logger.debug(
                        "thermo: skipping stale reading for %s age=%.1fs > %ss",
                        loc, age, max_stale,
                    )
                    continue

            try:
                temps.append(float(rec.temperature))
                used_locs.append(loc)
                if latest_ts is None:
                    latest_ts = getattr(rec, 'timestamp', None)
            except Exception:
                continue

        if not temps:
            logger.debug(
                "thermo: no fresh DB readings for control locations=%s", locs
            )
            return None

        t = sum(temps) / len(temps)
        logger.debug(
            "thermo: read temps %s -> avg=%.2f from used_locs=%s (sample ts=%s)",
            temps, t, used_locs, latest_ts,
        )

        self._temps.append(float(t))
        if len(self._temps) == 0:
            return None

        if self._cfg.smooth_window <= 1:
            logger.debug("thermo: raw temp=%.2f (no smoothing)", float(t))
            return float(t)

        smoothed = sum(self._temps) / len(self._temps)
        logger.debug(
            "thermo: smoothed temp=%.2f window=%d",
            smoothed, len(self._temps),
        )
        return smoothed

    def set_smooth_window(self, n: int) -> None:
        """Update smoothing window size."""
        try:
            v = int(n)
            if v < 1:
                v = 1
            self._cfg.smooth_window = v
            # Recreate smoothing buffer with new window size
            self._temps = deque(self._temps, maxlen=max(1, v))
        except Exception:
            pass

    def set_control_locations(self, locs: list[str]) -> None:
        """Set control locations for temperature reading."""
        try:
            names = [str(x).strip() for x in (locs or []) if str(x).strip()]
            if not names:
                names = [self._location]
            self._cfg.control_locations = json.dumps(names)
        except Exception as e:
            logger.debug("thermo: set_control_locations failed: %s", e)
