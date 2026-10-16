"""Server-owned FMI station-observation archive.

Runs on its own thread, independently of heating sessions, device telemetry and
browser visits. It never reads or replaces the planner's ``WeatherService``
cache and never calls heater services, so a slow, failing or malformed source
cannot delay Stop or change departure timing.

Every ten minutes each station is asked for a two-hour window aligned to the
ten-minute source interval. The whole returned series is parsed losslessly
(source text, per-field times, missing markers) and persisted through the
Controller together with the compressed original reply.

After a successful live request the station's history is recovered: the first
start fetches the preceding 24 hours, and any stretch of the last seven days
without a successful reply is re-requested in bounded six-hour windows aligned
to UTC, through the same parser and archive path. Coverage derives from the
retrieval history, so restarts resume and nothing older than the repair limit
is ever claimed recovered.
"""

from __future__ import annotations

import logging
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

from .fmi_wfs import NS, XLINK_HREF, AlignedCollector, MalformedReply, fetch_reply, iso_z
from .weather_service import PELMAA_ID, RENKO_ID, STOREDQUERY_ID

logger = logging.getLogger(__name__)

STATIONS = (str(PELMAA_ID), str(RENKO_ID))
# Labels until a reply supplies the station's own name.
STATION_LABELS = {
    str(PELMAA_ID): "Seinäjoki Pelmaa",
    str(RENKO_ID): "Ilmajoki Seinäjoki lentoasema",
}
PARAMETERS = (
    "t2m",
    "rh",
    "td",
    "ws_10min",
    "wd_10min",
    "wg_10min",
    "n_man",
    "r_1h",
    "ri_10min",
    "wawa",
    "snow_aws",
    "vis",
)
# Units and aggregation periods from FMI observableProperty metadata
# (checked 2026-10-16). wawa is a WMO 4680 present-weather code without a unit.
PARAMETER_META = {
    "t2m": ("degC", "PT1M"),
    "rh": ("%", "PT1M"),
    "td": ("degC", "PT1M"),
    "ws_10min": ("m/s", "PT10M"),
    "wd_10min": ("deg", "PT10M"),
    "wg_10min": ("m/s", "PT10M"),
    "n_man": ("1/8", "PT1M"),
    "r_1h": ("mm", "PT1H"),
    "ri_10min": ("mm/h", "PT10M"),
    "wawa": ("WMO 4680 code", "PT1M"),
    "snow_aws": ("cm", "PT1M"),
    "vis": ("m", "PT1M"),
}
# At a ten-minute step FMI pads hourly fields with NaN between the hours. Those
# padding slots are not source readings and are not archived.
REPORTING_MINUTES = {"r_1h": 60}

INTERVAL_S = 600
WINDOW = timedelta(hours=2)
TIMEOUT_S = 30
INITIAL_HISTORY = timedelta(hours=24)
REPAIR_LIMIT = timedelta(days=7)
REPAIR_CHUNK = timedelta(hours=6)
# Recovery requests per station per slot: a full seven-day repair takes about
# five slots and never pushes the next live request out of its slot.
REPAIR_REQUESTS_PER_SLOT = 6
PENDING_KINDS = ("failed", "not_collected")


@dataclass
class Reading:
    observed_at: str
    status: str  # valid | missing | invalid
    value: float | None
    source_value: str


@dataclass
class Series:
    fmisid: str | None
    parameter: str
    property_href: str | None
    location: dict[str, Any]
    readings: list[Reading] = field(default_factory=list)
    skipped: int = 0


def _reading(param: str, time_text: str, value_text: str) -> Reading | None:
    """Classify one source point; None for unusable times and hourly padding."""
    try:
        observed = datetime.fromisoformat(time_text.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return None
    except ValueError:
        return None
    observed = observed.astimezone(UTC)
    try:
        number = float(value_text)
    except ValueError:
        number = None
    if value_text == "NaN":
        step = REPORTING_MINUTES.get(param)
        if step and (observed.minute % step or observed.second):
            return None
        return Reading(observed.isoformat(), "missing", None, value_text)
    if number is None or not math.isfinite(number):
        return Reading(observed.isoformat(), "invalid", None, value_text)
    return Reading(observed.isoformat(), "valid", number, value_text)


def _location(obs: ET.Element) -> dict[str, Any]:
    info: dict[str, Any] = {}
    loc = obs.find(".//target:Location", NS)
    if loc is not None:
        ident = loc.find("gml:identifier", NS)
        if ident is not None and ident.text:
            info["fmisid"] = ident.text.strip()
        for name in loc.findall("gml:name", NS):
            kind = name.attrib.get("codeSpace", "").rsplit("/", 1)[-1]
            if kind and name.text:
                info[kind] = name.text.strip()
        region = loc.find("target:region", NS)
        if region is not None and region.text:
            info["region"] = region.text.strip()
    pos = obs.find(".//gml:Point/gml:pos", NS)
    if pos is not None and pos.text:
        parts = pos.text.split()
        if len(parts) == 2:
            info["pos"] = " ".join(parts)
    return info


def parse_observation_reply(content: bytes) -> list[Series]:
    """Parse an FMI timevaluepair reply without discarding source detail.

    An empty collection is a valid reply with no series. Anything that is not a
    WFS feature collection raises ``MalformedReply``.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise MalformedReply("XML parse failed") from exc
    if root.tag != f"{{{NS['wfs']}}}FeatureCollection":
        raise MalformedReply(f"Unexpected root element {root.tag!r}")
    series: list[Series] = []
    for member in root.findall("wfs:member", NS):
        obs = next(iter(member), None)
        if obs is None:
            continue
        prop = obs.find("om:observedProperty", NS)
        href = prop.attrib.get(XLINK_HREF) if prop is not None else None
        param = None
        if href and "param=" in href:
            param = href.split("param=", 1)[1].split("&", 1)[0].strip() or None
        if param is None:
            gml_id = obs.find(".//wml2:MeasurementTimeseries", NS)
            raw_id = gml_id.attrib.get(f"{{{NS['gml']}}}id", "") if gml_id is not None else ""
            param = raw_id.rsplit("-", 1)[-1] or None
        if param is None:
            logger.warning("FMI observation member without a parameter identity; skipped")
            continue
        location = _location(obs)
        entry = Series(location.get("fmisid"), param, href, location)
        for tvp in obs.findall(".//wml2:MeasurementTVP", NS):
            t_el, v_el = tvp.find("wml2:time", NS), tvp.find("wml2:value", NS)
            time_text = (t_el.text or "").strip() if t_el is not None else ""
            value_text = (v_el.text or "").strip() if v_el is not None else ""
            reading = _reading(param, time_text, value_text)
            if reading is None:
                entry.skipped += 1
            else:
                entry.readings.append(reading)
        series.append(entry)
    return series


class ObservationCollector(AlignedCollector):
    """Collect both stations every ten minutes on a server-owned thread."""

    def __init__(
        self,
        ctrl,
        *,
        http=None,
        clock: Callable[[], float] = time.time,
        stations: tuple[str, ...] = STATIONS,
        interval_s: int = INTERVAL_S,
        window: timedelta = WINDOW,
        timeout_s: float = TIMEOUT_S,
        repair_limit: timedelta = REPAIR_LIMIT,
        repair_requests: int = REPAIR_REQUESTS_PER_SLOT,
    ) -> None:
        super().__init__(clock=clock, interval_s=interval_s)
        self._ctrl = ctrl
        self._http = http or requests
        self.stations = tuple(str(s) for s in stations)
        self.window = window
        self._timeout_s = timeout_s
        self.repair_limit = repair_limit
        self._repair_requests = repair_requests

    def start(self) -> None:
        if self._thread is not None:
            return
        super().start()
        logger.info(
            "Weather observation collector started stations=%s interval_s=%s",
            ",".join(self.stations),
            self.interval_s,
        )

    # --- collection -------------------------------------------------------
    def collect(self, slot: datetime) -> None:
        """Collect every station live, then recover history for those that answered.

        Each station succeeds or fails alone, and no station's recovery delays
        another station's live request.
        """
        answered = []
        for fmisid in self.stations:
            progress = None
            try:
                # Before the live attempt, so a new archive's own first failure
                # cannot later pass for live-only history from an upgrade.
                progress = self._ctrl.ensure_weather_collection_progress(
                    "observation",
                    fmisid,
                    anchor_iso=slot.isoformat(),
                    now_iso=self.now().isoformat(),
                    lookback_s=INITIAL_HISTORY.total_seconds(),
                )
            except Exception as exc:
                self._archive_failed(fmisid, exc, "progress")
            try:
                live_ok = self._fetch(fmisid, slot, slot - self.window, slot, "live")
            except Exception as exc:
                # Persistence failed; the next overlapping window retries.
                self._archive_failed(fmisid, exc, "live")
                continue
            if progress is None:
                continue  # Without recorded progress there is no history to recover.
            self.archive_errors.pop(fmisid, None)
            if live_ok:
                answered.append((fmisid, progress))
            # Otherwise the source is failing now; recovery waits for a later slot.
        for fmisid, progress in answered:
            try:
                self._recover(fmisid, slot, progress)
            except Exception as exc:
                self._archive_failed(fmisid, exc, "recovery")

    def _archive_failed(self, fmisid: str, exc: Exception, stage: str) -> None:
        self.archive_errors[fmisid] = {
            "at": self.now().isoformat(),
            "error": type(exc).__name__,
            "stage": stage,
        }
        logger.exception("Weather archive write failed station=%s stage=%s", fmisid, stage)

    def coverage(self, fmisid: str, start: datetime, end: datetime) -> list[dict]:
        return self._ctrl.get_weather_observation_coverage(
            fmisid,
            start.isoformat(),
            end.isoformat(),
            step_s=self.interval_s,
            repair_from_iso=(self.now() - self.repair_limit).isoformat(),
        )

    def _recover(self, fmisid: str, slot: datetime, progress: dict) -> None:
        """Initialize or repair the station's history within the repair limit."""
        lo = max(datetime.fromisoformat(progress["history_start"]), slot - self.repair_limit)
        hi = slot + timedelta(seconds=self.interval_s)
        if lo >= hi:
            return
        pending = [s for s in self.coverage(fmisid, lo, hi) if s["kind"] in PENDING_KINDS]
        chunks = sorted({c for gap in pending for c in self._chunks(gap)})
        initializing = progress["initialized_at"] is None
        created = datetime.fromisoformat(progress["created_at"])
        if chunks:
            logger.info(
                "Weather observation recovery station=%s pending_windows=%d requesting=%d",
                fmisid,
                len(chunks),
                min(len(chunks), self._repair_requests),
            )
        attempted = 0
        for chunk in chunks[: self._repair_requests]:
            start, end = max(chunk, lo), min(chunk + REPAIR_CHUNK, slot)
            purpose = "initialization" if initializing and start < created else "repair"
            attempted += 1
            if not self._fetch(fmisid, slot, start, end, purpose):
                break  # Leave the rest for a later slot instead of pressing a failing source.
        updates: dict[str, Any] = {}
        if attempted:
            updates["last_repair_at"] = self.now().isoformat()
        if initializing:
            done = lo >= created or not any(
                s["kind"] in PENDING_KINDS for s in self.coverage(fmisid, lo, created)
            )
            if done:
                updates["initialized_at"] = self.now().isoformat()
                logger.info("Weather observation history initialized station=%s", fmisid)
        if updates:
            self._ctrl.update_weather_collection_progress(
                "observation", fmisid, now_iso=self.now().isoformat(), **updates
            )

    @staticmethod
    def _chunks(gap: dict) -> list[datetime]:
        """UTC-aligned repair windows overlapping one gap."""
        size = REPAIR_CHUNK.total_seconds()
        lo = datetime.fromisoformat(gap["start"]).timestamp()
        hi = datetime.fromisoformat(gap["end"]).timestamp()
        first = lo - (lo % size)
        return [
            datetime.fromtimestamp(first + i * size, UTC)
            for i in range(int((hi - first + size - 1) // size))
        ]

    def _fetch(
        self, fmisid: str, slot: datetime, start: datetime, end: datetime, purpose: str
    ) -> bool:
        """Request, parse and archive one window. True when the reply was usable."""
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": STOREDQUERY_ID,
            "fmisid": fmisid,
            "starttime": iso_z(start),
            "endtime": iso_z(end),
            "timestep": "10",
            "parameters": ",".join(PARAMETERS),
        }
        retrieval: dict[str, Any] = {
            "kind": "observation",
            "source_id": fmisid,
            "purpose": purpose,
            "intended_at": slot.isoformat(),
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "started_at": self.now().isoformat(),
            "request": {k: params[k] for k in ("storedquery_id", "timestep", "parameters")},
        }
        content, series = fetch_reply(
            self._http, params, self._timeout_s, parse_observation_reply, retrieval, self.now
        )
        if retrieval["status"] != "ok":
            logger.warning(
                "Weather observation collection failed station=%s purpose=%s error=%s "
                "http_status=%s",
                fmisid,
                purpose,
                retrieval.get("error"),
                retrieval.get("http_status"),
            )
        self._ctrl.record_weather_observation_retrieval(
            retrieval, content, series, requested=PARAMETERS, meta=PARAMETER_META
        )
        return retrieval["status"] == "ok"
