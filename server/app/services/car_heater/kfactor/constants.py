"""
KFactor calibration constants and utility functions.

Physical constants for thermal modeling and common helper functions.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ==========================================================================
# PHYSICAL CONSTANTS
# ==========================================================================

CABIN_VOLUME_M3 = 2.8
HEATER_POWER_W = 1000.0
AIR_DENSITY_KG_M3 = 1.2
SPECIFIC_HEAT_J_KG_K = 1000.0
HEAT_CAPACITY_J_PER_K = AIR_DENSITY_KG_M3 * \
    CABIN_VOLUME_M3 * SPECIFIC_HEAT_J_KG_K


# ==========================================================================
# UTILITY FUNCTIONS
# ==========================================================================

def clamp(x: float, lo: float, hi: float) -> float:
    """Clamp x to [lo, hi]."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def finite(x: float | None) -> float | None:
    """Return x if finite, else None."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def dt_from_any(ts: Any, tz: ZoneInfo) -> datetime | None:
    """Parse a datetime from various formats."""
    if isinstance(ts, datetime):
        return ts.astimezone(tz) if ts.tzinfo else ts.replace(tzinfo=tz)
    if isinstance(ts, str) and ts.strip():
        try:
            dt = datetime.fromisoformat(ts.strip())
            return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
        except Exception:
            logger.debug("dt_from_any: failed to parse timestamp '%s'", ts)
            return None
    return None


def parse_hhmm(raw: str) -> tuple[int, int] | None:
    """Parse 'HH:MM' string to (hour, minute) tuple."""
    try:
        parts = raw.strip().split(":", 1)
        if len(parts) != 2:
            return None
        hh = int(parts[0])
        mm = int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh, mm
    except Exception:
        logger.debug("parse_hhmm: failed to parse time '%s'", raw)
        return None


def iso_no_micros(dt: datetime) -> str:
    """Format datetime as ISO string without microseconds."""
    return dt.replace(microsecond=0).isoformat(sep=" ")


def bucket_key(outside_temp_c: float, wind_m_s: float | None) -> tuple[int, int | None]:
    """Compute temperature and wind bucket keys."""
    t_bucket = int(round(outside_temp_c / 2.0) * 2)
    wind_bucket = int(round(wind_m_s / 2.0) *
                      2) if wind_m_s is not None else None
    return t_bucket, wind_bucket


def linspace(lo: float, hi: float, n: int) -> list[float]:
    """Generate n evenly spaced values from lo to hi."""
    if n <= 1:
        return [float(lo)]
    step = (hi - lo) / float(n - 1)
    return [float(lo + i * step) for i in range(n)]
