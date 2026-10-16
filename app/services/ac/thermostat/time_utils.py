"""
Time utilities for thermostat.

ISO timestamp parsing, HHMM time parsing, epoch conversions.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytz

logger = logging.getLogger(__name__)


def parse_iso_to_epoch(s: str | None, tz: pytz.timezone) -> float | None:
    """Parse ISO timestamp string to epoch seconds."""
    if not s:
        return None
    try:
        x = str(s).strip()
        # Handle 'Z' while avoiding double offsets like '+00:00Z'
        if x.endswith("Z"):
            x = x[:-1]
        # datetime.fromisoformat can't parse 'Z', but can parse '+00:00'.
        # If no explicit offset remains, assume local tz.
        dt = datetime.fromisoformat(x)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.timestamp()
    except Exception as e:
        logger.debug("thermo: failed to parse ISO timestamp %s: %s", s, e)
        return None


def parse_hhmm_to_minutes(s: str | None) -> int | None:
    """Parse 'HH:MM' string to minutes since midnight."""
    if not s:
        return None
    s = s.strip()
    try:
        parts = s.split(":", 1)
        if len(parts) != 2:
            return None
        h = int(parts[0])
        m = int(parts[1])
        if not (0 <= h < 24 and 0 <= m < 60):
            return None
        return h * 60 + m
    except Exception:
        return None


def epoch_to_hhmm(epoch: float, tz: pytz.timezone) -> str:
    """Convert epoch seconds to 'HH:MM' string in local timezone."""
    try:
        dt = datetime.fromtimestamp(epoch, tz=tz)
        return dt.strftime("%H:%M")
    except Exception:
        return "??:??"


def now_minutes_local() -> int:
    """Get current time as minutes since midnight (local time)."""
    lt = time.localtime()
    return lt.tm_hour * 60 + lt.tm_min


def compute_phase_duration(
    start_iso: str | None,
    tz: pytz.timezone,
    output_format: str = "minutes",
) -> int | None:
    """Compute phase duration from ISO timestamp to now.

    Args:
        start_iso: ISO timestamp string of phase start
        tz: Timezone for parsing
        output_format: 'minutes' or 'seconds'

    Returns:
        Duration in minutes/seconds, or None if invalid
    """
    if not start_iso:
        return None
    try:
        s = str(start_iso).strip()
        if s.endswith("Z"):
            s = s[:-1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        phase_s = max(0.0, time.time() - dt.timestamp())
        if output_format == "minutes":
            return int(phase_s) // 60 if phase_s >= 60 else None
        return int(phase_s)
    except Exception as e:
        logger.debug("thermo: compute_phase_duration failed for %s: %s", start_iso, e)
        return None
