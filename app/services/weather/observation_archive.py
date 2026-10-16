"""Server-owned FMI station-observation archive.

Runs on its own thread, independently of heating sessions, device telemetry and
browser visits. It never reads or replaces the planner's ``WeatherService``
cache and never calls heater services, so a slow, failing or malformed source
cannot delay Stop or change departure timing.

Every ten minutes each station is asked for a two-hour window aligned to the
ten-minute source interval. The whole returned series is parsed losslessly
(source text, per-field times, missing markers) and persisted through the
Controller together with the compressed original reply.
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
    ) -> None:
        super().__init__(clock=clock, interval_s=interval_s)
        self._ctrl = ctrl
        self._http = http or requests
        self.stations = tuple(str(s) for s in stations)
        self.window = window
        self._timeout_s = timeout_s

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
        """Collect every station for one slot; each station succeeds or fails alone."""
        for fmisid in self.stations:
            try:
                self._collect_station(fmisid, slot)
            except Exception as exc:
                # Persistence failed; the next overlapping window retries.
                self.archive_errors[fmisid] = {
                    "at": self.now().isoformat(),
                    "error": type(exc).__name__,
                }
                logger.exception("Weather archive write failed station=%s", fmisid)

    def _collect_station(self, fmisid: str, slot: datetime) -> None:
        start = slot - self.window
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": STOREDQUERY_ID,
            "fmisid": fmisid,
            "starttime": iso_z(start),
            "endtime": iso_z(slot),
            "timestep": "10",
            "parameters": ",".join(PARAMETERS),
        }
        retrieval: dict[str, Any] = {
            "kind": "observation",
            "source_id": fmisid,
            "intended_at": slot.isoformat(),
            "requested_start": start.isoformat(),
            "requested_end": slot.isoformat(),
            "started_at": self.now().isoformat(),
            "request": {k: params[k] for k in ("storedquery_id", "timestep", "parameters")},
        }
        content, series = fetch_reply(
            self._http, params, self._timeout_s, parse_observation_reply, retrieval, self.now
        )
        if retrieval["status"] != "ok":
            logger.warning(
                "Weather observation collection failed station=%s error=%s http_status=%s",
                fmisid,
                retrieval.get("error"),
                retrieval.get("http_status"),
            )
        self._ctrl.record_weather_observation_retrieval(
            retrieval, content, series, requested=PARAMETERS, meta=PARAMETER_META
        )
        self.archive_errors.pop(fmisid, None)
