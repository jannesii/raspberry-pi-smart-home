"""FMI-observation duration plans with immutable Helsinki departure timelines."""

from __future__ import annotations

import copy
import logging
import math
import time
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
logger = logging.getLogger(__name__)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def departure_epoch(value, fold=None) -> float:
    if not isinstance(value, str):
        raise ValueError("Departure must be a date and time")
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError("Repeated time occurrence must be 0 or 1")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Departure must be a valid date and time") from None
    if parsed.tzinfo is not None:
        return parsed.timestamp()
    choices = {}
    for occurrence in (0, 1):
        candidate = parsed.replace(tzinfo=HELSINKI, fold=occurrence)
        if candidate.astimezone(UTC).astimezone(HELSINKI).replace(tzinfo=None) == parsed:
            choices[candidate.timestamp()] = occurrence
    if not choices:
        raise ValueError("This time does not exist in Europe/Helsinki")
    if len(choices) > 1:
        if isinstance(fold, bool) or fold not in (0, 1):
            raise ValueError("This time occurs twice in Europe/Helsinki; choose its occurrence")
        return parsed.replace(tzinfo=HELSINKI, fold=fold).timestamp()
    return next(iter(choices))


class DepartureService:
    def __init__(self, ctrl, heater, weather=None, *, clock=time.time, weather_max_age_s=1800):
        self._ctrl = ctrl
        self._heater = heater
        self._weather = weather
        self._clock = clock
        self._weather_max_age_s = weather_max_age_s
        self._lock = RLock()
        self._plan = ctrl.get_departure_plan()
        if (
            self._plan
            and self._plan["status"] in ("scheduled", "waiting_for_device", "running", "stopping")
            and ctrl.get_car_heater_execution(self._plan["session_id"])
        ):
            self._event("interrupted", "server_restart_never_resumes_on")
            self.tick()

    def weather_snapshot(self, *, refresh=True):
        now = self._clock()
        snapshot = {
            "source": "FMI observations",
            "captured_at": iso(now),
            "observed_at": None,
            "temperature_c": None,
            "station_name": None,
            "fmisid": None,
            "max_age_s": self._weather_max_age_s,
            "age_s": None,
            "fallback_reason": "Weather unavailable",
        }
        try:
            data = (
                (self._weather.get_latest() if refresh else self._weather.get_cached())
                if self._weather
                else None
            )
            if data is None:
                return snapshot
            snapshot.update(station_name=data.station_name, fmisid=data.fmisid)
            reading = data.t2m
            if reading is None:
                return snapshot
            value = reading.value
            observed = datetime.fromisoformat(reading.time)
            snapshot["observed_at"] = reading.time
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not -80 <= value <= 60
            ):
                snapshot["fallback_reason"] = "Invalid outside temperature"
                return snapshot
            snapshot["temperature_c"] = value
            if observed.tzinfo is None:
                snapshot["fallback_reason"] = "Observation time has no timezone"
                return snapshot
            age = now - observed.timestamp()
            snapshot["age_s"] = age
            snapshot["fallback_reason"] = (
                "Observation is in the future"
                if age < 0
                else "Observation is older than the freshness limit"
                if age > self._weather_max_age_s
                else None
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            snapshot["fallback_reason"] = "Invalid weather observation"
            logger.debug("Invalid departure weather observation", exc_info=True)
        except Exception:
            snapshot["fallback_reason"] = "Weather unavailable"
            logger.debug("Departure weather unavailable", exc_info=True)
        return snapshot

    def preview(self, departure_at, *, fold=None, grace_minutes=30):
        departure = departure_epoch(departure_at, fold)
        if departure <= self._clock():
            raise ValueError("Choose a future departure")
        if (
            isinstance(grace_minutes, bool)
            or not isinstance(grace_minutes, int)
            or not 0 <= grace_minutes <= 30
        ):
            raise ValueError("Grace must be a whole number from 0 to 30 minutes")
        weather = self.weather_snapshot()
        duration = (
            180
            if weather["fallback_reason"]
            else math.ceil(max(90, min(180, 90 - 6 * weather["temperature_c"])))
        )
        grace = 0 if weather["fallback_reason"] else min(grace_minutes, 180 - duration)
        return {
            "fold": fold,
            "departure_at": iso(departure),
            "planned_start_at": iso(departure - duration * 60),
            "finish_at": iso(departure + grace * 60),
            "preheat_minutes": duration,
            "grace_minutes": grace,
            "weather": weather,
            "fallback_reason": weather["fallback_reason"],
        }

    def get_plan(self):
        with self._lock:
            return copy.deepcopy(self._plan)

    def save(self, departure_at, *, fold=None, grace_minutes=30, expected_preview=None):
        preview = self.preview(departure_at, fold=fold, grace_minutes=grace_minutes)
        if expected_preview is not None:
            keys = (
                "departure_at",
                "planned_start_at",
                "finish_at",
                "preheat_minutes",
                "grace_minutes",
                "fallback_reason",
            )
            if not isinstance(expected_preview, dict) or any(
                expected_preview.get(key) != preview[key] for key in keys
            ):
                raise ValueError("Weather or timing changed; preview departure again before saving")
        with self._lock:
            if self._plan and self._plan["status"] in ("running", "stopping", "interrupted"):
                self.tick()
            self._heater.require_mode_available()
            now = iso(self._clock())
            plan = {
                **preview,
                "session_id": str(uuid4()),
                "created_at": now,
                "supersedes": self._plan["session_id"] if self._plan else None,
                "status": "scheduled",
                "weather_snapshots": {"planning": preview["weather"]},
                "events": [{"kind": "saved", "at": now, "reason": "driver_saved"}],
            }
            self._ctrl.create_departure_revision(plan)
            self._plan = plan
            logger.info(
                "Saved departure revision session=%s finish=%s",
                plan["session_id"],
                plan["finish_at"],
            )
            return copy.deepcopy(plan)

    def cancel(self):
        with self._lock:
            if not self._plan:
                return None
            if self._plan["status"] in ("scheduled", "waiting_for_device"):
                self._event("cancelled", "driver_cancelled")
            elif self._plan["status"] == "running":
                self._heater.stop_bounded_session("departure_cancelled")
                self._event("stopping", "driver_cancelled")
            return copy.deepcopy(self._plan)

    def _event(self, status, reason):
        self._plan["status"] = status
        self._plan["events"].append({"kind": status, "at": iso(self._clock()), "reason": reason})
        self._ctrl.save_departure_plan(self._plan)
        logger.debug(
            "Departure session=%s status=%s reason=%s", self._plan["session_id"], status, reason
        )

    def tick(self):
        with self._lock:
            plan = self._plan
            if not plan:
                return
            now = self._clock()
            # A departure observation may be unavailable or delayed after downtime;
            # retain its actual capture time instead of inventing historical weather.
            if (
                now >= datetime.fromisoformat(plan["departure_at"]).timestamp()
                and "departure" not in plan["weather_snapshots"]
            ):
                plan["weather_snapshots"]["departure"] = self.weather_snapshot(refresh=False)
                self._ctrl.save_departure_plan(plan)
            if plan["status"] in ("running", "stopping", "interrupted"):
                run = self._heater.get_bounded_session()
                if not run or run["session_id"] != plan["session_id"]:
                    run = self._ctrl.get_car_heater_execution(plan["session_id"])
                if run and run["phase"] == "stopped":
                    reason = next(
                        (
                            e["reason"]
                            for e in reversed(run["milestones"])
                            if e["kind"] == "off_requested"
                        ),
                        "relay_local_finish",
                    )
                    self._event(
                        "completed" if reason in ("expired", "relay_local_finish") else "cancelled",
                        reason,
                    )
                elif run and run["phase"] == "stopping" and plan["status"] == "running":
                    self._event("stopping", "bounded_off_requested")
                return
            if plan["status"] not in ("scheduled", "waiting_for_device"):
                return
            if now >= datetime.fromisoformat(plan["finish_at"]).timestamp():
                self._event("expired", "original_finish_passed_without_start")
                return
            if now < datetime.fromisoformat(plan["planned_start_at"]).timestamp():
                return
            try:
                # Persisted execution prevents ON replay if the server dies
                # before the plan's running milestone is written.
                run = self._heater.start_departure_session(plan["session_id"])
            except ValueError as exc:
                if plan["status"] != "waiting_for_device":
                    self._event("waiting_for_device", str(exc))
                return
            plan["weather_snapshots"]["start"] = self.weather_snapshot(refresh=False)
            plan["actual_start_requested_at"] = iso(run["issued_at"])
            plan["late_start_seconds"] = max(
                0, now - datetime.fromisoformat(plan["planned_start_at"]).timestamp()
            )
            self._event(
                "running", "late_start" if plan["late_start_seconds"] > 0 else "planned_start"
            )
