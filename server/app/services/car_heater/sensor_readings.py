"""Glass and cabin sensor readings reported by the car-heater firmware.

Firmware 3.0.0+ sends a ``sensors`` object with ``glass_reference``,
``glass_comparison`` and ``cabin`` entries. Each entry has ``status``
(``ok``/``cached``/``missing``), ``temperature_c``, ``age_ms`` and, for the
cabin, ``humidity_pct``. The tracker turns those reports into ingest
outcomes for persistence and a live view whose freshness is judged when it
is read, so a cached or old value never looks current.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from ...core.models import CarHeaterSensorFrame

logger = logging.getLogger(__name__)

SENSOR_KEYS = ("glass_reference", "glass_comparison", "cabin")
HUMIDITY_SENSOR = "cabin"

TEMPERATURE_RANGE_C = (-50.0, 100.0)
HUMIDITY_RANGE_PCT = (0.0, 100.0)
DEFAULT_MAX_AGE_S = 60.0
# Two reports of one sample (frame times have 1 s resolution) differ by < 1 s;
# distinct samples are at least the firmware's 1 s sampling interval apart.
MIN_NEW_SAMPLE_GAP_S = 1.5
# Tolerated device-clock lead before a sample time counts as invalid.
MAX_FUTURE_SKEW_S = 30.0


def sensor_max_age_s() -> float:
    """Maximum sample age that still counts as fresh (CAR_HEATER_SENSOR_MAX_AGE_S)."""
    raw = os.getenv("CAR_HEATER_SENSOR_MAX_AGE_S")
    if raw is None:
        return DEFAULT_MAX_AGE_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            "Invalid CAR_HEATER_SENSOR_MAX_AGE_S=%r; using %.0f s", raw, DEFAULT_MAX_AGE_S
        )
        return DEFAULT_MAX_AGE_S
    return value


def iso_utc(value: datetime) -> str:
    """Fixed-width UTC ISO text, so stored timestamps compare as strings."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class SensorObservation:
    """Outcome of one reported sensor entry.

    ``state``: ``ok`` (accepted new sample), ``cached`` (device retained an
    earlier value after a failed read), ``missing``, ``invalid``, ``stale``
    (older than the freshness limit on arrival) or ``out_of_order``.
    """

    state: str
    temperature_c: float | None = None
    humidity_pct: float | None = None
    sampled_at: datetime | None = None
    error: str | None = None
    # A sample the device just read and the server has not stored before.
    # Late (stale) ones are kept for history but never shown as current.
    new_sample: bool = False


@dataclass
class _LiveSensor:
    temperature_c: float | None = None
    humidity_pct: float | None = None
    sampled_at: datetime | None = None
    last_state: str = "missing"
    cached: bool = False
    error: str | None = None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _in_range(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def normalize_temperature_c(raw: Any) -> float | None:
    """A finite temperature within TEMPERATURE_RANGE_C, otherwise unavailable."""
    value = _finite(raw)
    return value if value is not None and _in_range(value, TEMPERATURE_RANGE_C) else None


class CarHeaterSensorTracker:
    """Latest accepted glass/cabin readings, shared by the HTTP and WS paths."""

    def __init__(
        self,
        *,
        max_age_s: float | None = None,
        seed: Mapping[str, CarHeaterSensorFrame] | None = None,
    ) -> None:
        self.max_age_s = max_age_s if max_age_s is not None else sensor_max_age_s()
        self._lock = Lock()
        self._live: dict[str, _LiveSensor] = {}
        # Sample time of the newest stored sample per sensor, for ordering.
        self._newest_sample_at: dict[str, datetime] = {}
        for key, frame in (seed or {}).items():
            self._seed(key, frame)

    def _seed(self, key: str, frame: CarHeaterSensorFrame) -> None:
        sampled_raw = getattr(frame, f"{key}_sampled_at", None)
        temperature = getattr(frame, f"{key}_temp_c", None)
        if key not in SENSOR_KEYS or sampled_raw is None or temperature is None:
            return
        try:
            sampled_at = datetime.fromisoformat(sampled_raw)
        except ValueError:
            return
        self._live[key] = _LiveSensor(
            temperature_c=temperature,
            humidity_pct=frame.cabin_humidity_pct if key == HUMIDITY_SENSOR else None,
            sampled_at=sampled_at,
            last_state="ok",
        )
        self._newest_sample_at[key] = sampled_at

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(
        self,
        sensors: Any,
        *,
        frame_ts: datetime,
        received_at: datetime,
    ) -> dict[str, SensorObservation]:
        """Apply one frame's ``sensors`` object; omitted sensors are untouched."""
        if not isinstance(sensors, Mapping):
            if sensors is not None:
                logger.warning("Ignoring non-object car heater sensors payload")
            return {}

        observations: dict[str, SensorObservation] = {}
        with self._lock:
            for key in SENSOR_KEYS:
                if key not in sensors:
                    continue
                observation = self._observe(
                    key,
                    sensors[key],
                    frame_ts=frame_ts,
                    received_at=received_at,
                    newest_sample_at=self._newest_sample_at.get(key),
                )
                self._apply(key, observation)
                observations[key] = observation
                if observation.state not in ("ok", "cached"):
                    logger.debug(
                        "Car heater sensor %s rejected state=%s error=%s",
                        key,
                        observation.state,
                        observation.error,
                    )
        return observations

    def _observe(
        self,
        key: str,
        raw: Any,
        *,
        frame_ts: datetime,
        received_at: datetime,
        newest_sample_at: datetime | None,
    ) -> SensorObservation:
        if not isinstance(raw, Mapping):
            return SensorObservation("invalid", error="malformed")
        status = raw.get("status")
        error = raw.get("error") if isinstance(raw.get("error"), str) else None
        if status == "missing":
            return SensorObservation("missing", error=error)
        if status not in ("ok", "cached"):
            return SensorObservation("invalid", error="unknown_status")

        temperature = _finite(raw.get("temperature_c"))
        if temperature is None or not _in_range(temperature, TEMPERATURE_RANGE_C):
            return SensorObservation("invalid", error="temperature_out_of_range")
        humidity = None
        if key == HUMIDITY_SENSOR:
            humidity = _finite(raw.get("humidity_pct"))
            if humidity is None or not _in_range(humidity, HUMIDITY_RANGE_PCT):
                return SensorObservation("invalid", error="humidity_out_of_range")
        age_ms = _finite(raw.get("age_ms"))
        if age_ms is None or age_ms < 0:
            return SensorObservation("invalid", error="bad_age")

        sampled_at = frame_ts - timedelta(milliseconds=age_ms)
        values = {"temperature_c": temperature, "humidity_pct": humidity, "sampled_at": sampled_at}
        if sampled_at > received_at + timedelta(seconds=MAX_FUTURE_SKEW_S):
            return SensorObservation("invalid", error="future_timestamp")
        stale = (received_at - sampled_at).total_seconds() > self.max_age_s
        if status == "cached":
            return SensorObservation("stale" if stale else "cached", error=error, **values)
        if newest_sample_at is not None and sampled_at <= newest_sample_at + timedelta(
            seconds=MIN_NEW_SAMPLE_GAP_S
        ):
            return SensorObservation("out_of_order", **values)
        return SensorObservation("stale" if stale else "ok", new_sample=True, **values)

    def _apply(self, key: str, observation: SensorObservation) -> None:
        state = observation.state
        if observation.new_sample:
            # Includes late samples, so a resend is not stored twice.
            self._newest_sample_at[key] = observation.sampled_at
        if state in ("stale", "out_of_order"):
            return  # never replaces the live reading
        live = self._live.setdefault(key, _LiveSensor())
        if state == "ok":
            live.temperature_c = observation.temperature_c
            live.humidity_pct = observation.humidity_pct
            live.sampled_at = observation.sampled_at
            live.cached = False
            live.error = None
            live.last_state = state
        elif state == "cached":
            if live.temperature_c is None:
                live.temperature_c = observation.temperature_c
                live.humidity_pct = observation.humidity_pct
                live.sampled_at = observation.sampled_at
            live.cached = True
            live.error = observation.error
            live.last_state = state
        elif state == "missing":
            live.temperature_c = None
            live.humidity_pct = None
            live.sampled_at = None
            live.cached = False
            live.error = observation.error
            live.last_state = state
        elif state == "invalid":
            # The current read is unusable; keep any earlier value, which ages.
            live.cached = live.temperature_c is not None
            live.error = observation.error
            live.last_state = state

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def snapshot(self, now: datetime) -> dict[str, dict[str, Any]]:
        """Live readings for the browser; freshness is evaluated at ``now``."""
        with self._lock:
            return {key: self._describe(key, live, now) for key, live in self._live.items()}

    def _describe(self, key: str, live: _LiveSensor, now: datetime) -> dict[str, Any]:
        age_s = None
        if live.temperature_c is None or live.sampled_at is None:
            state = "invalid" if live.last_state == "invalid" else "missing"
        else:
            age_s = round(max(0.0, (now - live.sampled_at).total_seconds()), 1)
            state = "fresh" if age_s <= self.max_age_s else "stale"
        described: dict[str, Any] = {
            "state": state,
            "temperature_c": live.temperature_c,
            "sampled_at": iso_utc(live.sampled_at) if live.sampled_at else None,
            "age_s": age_s,
            "cached": live.cached,
            "error": live.error,
            "max_age_s": self.max_age_s,
        }
        if key == HUMIDITY_SENSOR:
            described["humidity_pct"] = live.humidity_pct
        return described


def build_sensor_frame(
    observations: Mapping[str, SensorObservation],
    *,
    received_at: datetime,
    frame_ts: datetime | None,
    source: str | None,
    firmware_version: str | None,
) -> CarHeaterSensorFrame:
    """Persistable record of one frame; values are kept only for new samples."""
    frame = CarHeaterSensorFrame(
        id=None,
        received_at=iso_utc(received_at),
        frame_timestamp=iso_utc(frame_ts) if frame_ts else None,
        source=source,
        firmware_version=firmware_version,
    )
    for key, observation in observations.items():
        accepted = observation.new_sample
        setattr(frame, f"{key}_state", observation.state)
        setattr(frame, f"{key}_temp_c", observation.temperature_c if accepted else None)
        setattr(
            frame,
            f"{key}_sampled_at",
            iso_utc(observation.sampled_at) if observation.sampled_at else None,
        )
        if key == HUMIDITY_SENSOR:
            frame.cabin_humidity_pct = observation.humidity_pct if accepted else None
    return frame
