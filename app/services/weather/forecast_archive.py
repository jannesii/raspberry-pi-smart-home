"""Server-owned FMI HARMONIE point-forecast archive for the parking point.

Runs on its own thread like the observation collector and shares none of the
planner's state: it never reads or refreshes ``WeatherService`` and never calls
heater services. No forecast or radiation field is a control input.

Every thirty minutes the latest point forecast is requested for a window far
wider than any run (FMI's default reply is a 50-hour window that moves with the
clock and cuts the run short). FMI fills the window outside the run with NaN;
that filler is counted, not stored, so the archive keeps exactly the hours the
run holds without hardcoding a horizon or model update cycle. Each distinct
version actually
received is kept with its model origin (analysis time), source result time,
per-value valid times and the retrievals that delivered it. Runs published and
replaced between checks are never received and are not reconstructed.
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

from .fmi_wfs import NS, XLINK_HREF, AlignedCollector, MalformedReply, fetch_reply, iso_z
from .observation_archive import TIMEOUT_S

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

STOREDQUERY_ID = "fmi::forecast::harmonie::surface::point::timevaluepair"
PARAMETERS = (
    "Temperature",
    "DewPoint",
    "Humidity",
    "WindSpeedMS",
    "WindDirection",
    "WindGust",
    "TotalCloudCover",
    "RadiationLW",
    "RadiationGlobal",
    "Precipitation1h",
    "WeatherSymbol3",
)
# Unit, aggregation period and statistic from FMI observableProperty=forecast
# metadata (checked 2026-10-16). The reply carries none of these; the original
# metadata link is kept per value. RadiationLW is FMI's "Long wave radiation";
# its direction (downwelling, net, ...) is not stated and not inferred here.
PARAMETER_META: dict[str, tuple[str | None, str | None, str | None]] = {
    "Temperature": ("degC", None, None),
    "DewPoint": ("degC", None, None),
    "Humidity": ("%", None, None),
    "WindSpeedMS": ("m/s", "PT1H", "avg"),
    "WindDirection": ("deg", "PT1H", "avg"),
    "WindGust": ("m/s", "PT1H", "max"),
    "TotalCloudCover": ("%", None, None),
    "RadiationLW": ("W/m2", None, None),
    "RadiationGlobal": ("W/m2", None, None),
    "Precipitation1h": ("mm/h", "PT1H", "sum"),
    "WeatherSymbol3": ("index", None, None),
}
# Stored with each request configuration. Bump it whenever PARAMETER_META
# changes, so a local metadata update starts a new configuration instead of
# appearing as an FMI revision of the same forecast.
METADATA_VERSION = "fmi-forecast-meta-2026-10-16"

INTERVAL_S = 1800
# No successful check for longer than these multiples of the interval is a
# visible unobserved stretch, and a stale collector, respectively.
UNOBSERVED_AFTER = 1.5
STALE_AFTER_INTERVALS = 2.5
TIMESTEP_MINUTES = "60"
# Requested around each check: wide enough to hold a whole run, which starts
# hours before it is published and ends a few days later.
WINDOW_BEFORE = timedelta(hours=24)
WINDOW_AFTER = timedelta(days=10)
WINDOW_LABEL = f"-PT{WINDOW_BEFORE // timedelta(hours=1)}H/P{WINDOW_AFTER.days}D"
_ANALYSIS_TIME = "analysisTime"


@dataclass
class ForecastValue:
    valid_at: str
    status: str  # valid | missing | invalid
    value: float | None
    source_value: str


@dataclass
class ForecastSeries:
    parameter: str
    property_href: str | None
    procedure: str | None
    analysis_time: str | None
    result_time: str | None
    phenomenon_start: str | None
    phenomenon_end: str | None
    location: dict[str, Any]
    values: list[ForecastValue] = field(default_factory=list)
    skipped: int = 0
    outside_run: int = 0


@dataclass(frozen=True)
class ForecastPoint:
    """A WGS84 forecast point; its ``lat,lon`` text is the archive source identity."""

    lat: float
    lon: float

    @classmethod
    def parse(cls, text: str | None) -> ForecastPoint:
        """``"lat,lon"`` in decimal degrees; empty means the recorded parking point."""
        if not text or not text.strip():
            return PARKING_POINT
        point = cls.from_id(text)
        if point is None:
            raise ValueError("Use decimal degrees as lat,lon within range")
        return point

    @classmethod
    def from_id(cls, text: str | None) -> ForecastPoint | None:
        """The point a source id or request ``latlon`` names; None when unusable."""
        try:
            lat, lon = (float(part) for part in (text or "").split(","))
        except ValueError:
            return None
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return cls(lat, lon)

    @property
    def id(self) -> str:
        return f"{self.lat:.4f},{self.lon:.4f}"

    def as_dict(self) -> dict[str, float]:
        return {"lat": self.lat, "lon": self.lon}


# The recorded parking point. A different point is a different source identity,
# so evidence from different points never merges.
PARKING_POINT = ForecastPoint(62.8000, 22.8260)


def _utc(text: str | None) -> str | None:
    """Normalise a source timestamp to UTC ISO text; None when absent or unusable."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _text(el: ET.Element | None) -> str | None:
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _value(time_text: str, value_text: str) -> ForecastValue | None:
    valid_at = _utc(time_text)
    if valid_at is None:
        return None
    if value_text == "NaN":
        return ForecastValue(valid_at, "missing", None, value_text)
    try:
        number = float(value_text)
    except ValueError:
        number = None
    if number is None or not math.isfinite(number):
        return ForecastValue(valid_at, "invalid", None, value_text)
    return ForecastValue(valid_at, "valid", number, value_text)


def _location(obs: ET.Element) -> dict[str, Any]:
    """Returned forecast location: identifier with its code space, names, position."""
    info: dict[str, Any] = {}
    loc = obs.find(".//target:Location", NS)
    if loc is not None:
        ident = loc.find("gml:identifier", NS)
        if ident is not None and ident.text:
            kind = ident.attrib.get("codeSpace", "").rsplit("/", 1)[-1] or "identifier"
            info[kind] = ident.text.strip()
        for name in loc.findall("gml:name", NS):
            kind = name.attrib.get("codeSpace", "").rsplit("/", 1)[-1]
            if kind and name.text:
                info.setdefault(kind, name.text.strip())
        for tag in ("region", "country", "timezone"):
            value = _text(loc.find(f"target:{tag}", NS))
            if value:
                info[tag] = value
    pos = obs.find(".//gml:Point/gml:pos", NS)
    if pos is not None and pos.text and len(pos.text.split()) == 2:
        info["pos"] = " ".join(pos.text.split())
    return info


def _resolve(el: ET.Element | None, by_id: dict[str, ET.Element]) -> ET.Element | None:
    """Follow a local ``xlink:href="#id"`` reference; FMI states shared times once."""
    if el is None or len(el):
        return el
    href = el.attrib.get(XLINK_HREF, "")
    return by_id.get(href[1:]) if href.startswith("#") else el


def _time_in(obs: ET.Element, path: str, tag: str, by_id: dict[str, ET.Element]) -> str | None:
    holder = _resolve(obs.find(path, NS), by_id)
    return _utc(_text(holder.find(f".//gml:{tag}", NS))) if holder is not None else None


def _analysis_time(obs: ET.Element, by_id: dict[str, ET.Element]) -> str | None:
    for named in obs.findall("om:parameter/om:NamedValue", NS):
        name = named.find("om:name", NS)
        href = name.attrib.get(XLINK_HREF, "") if name is not None else ""
        if href.rsplit("/", 1)[-1] == _ANALYSIS_TIME:
            return _time_in(named, "om:value", "timePosition", by_id)
    return None


def parse_forecast_reply(content: bytes) -> list[ForecastSeries]:
    """Parse an FMI point-forecast timevaluepair reply without discarding detail.

    An empty collection is a valid reply with no series. Anything that is not a
    WFS feature collection raises ``MalformedReply``.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise MalformedReply("XML parse failed") from exc
    if root.tag != f"{{{NS['wfs']}}}FeatureCollection":
        raise MalformedReply(f"Unexpected root element {root.tag!r}")
    gml_id = f"{{{NS['gml']}}}id"
    by_id = {el.attrib[gml_id]: el for el in root.iter() if gml_id in el.attrib}
    series: list[ForecastSeries] = []
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
            logger.warning("FMI forecast member without a parameter identity; skipped")
            continue
        procedure = obs.find("om:procedure", NS)
        entry = ForecastSeries(
            parameter=param,
            property_href=href,
            procedure=procedure.attrib.get(XLINK_HREF) if procedure is not None else None,
            analysis_time=_analysis_time(obs, by_id),
            result_time=_time_in(obs, "om:resultTime", "timePosition", by_id),
            phenomenon_start=_time_in(obs, "om:phenomenonTime", "beginPosition", by_id),
            phenomenon_end=_time_in(obs, "om:phenomenonTime", "endPosition", by_id),
            location=_location(obs),
        )
        for tvp in obs.findall(".//wml2:MeasurementTVP", NS):
            t_el, v_el = tvp.find("wml2:time", NS), tvp.find("wml2:value", NS)
            time_text = (t_el.text or "").strip() if t_el is not None else ""
            value_text = (v_el.text or "").strip() if v_el is not None else ""
            value = _value(time_text, value_text)
            if value is None:
                entry.skipped += 1
            else:
                entry.values.append(value)
        series.append(entry)
    return _trim_to_run(series)


def _trim_to_run(series: list[ForecastSeries]) -> list[ForecastSeries]:
    """Drop the NaN filler FMI returns for requested hours outside the run.

    The run spans its earliest to latest real value across the reply; every
    point inside, including a missing value, is kept. With no real value at all
    nothing can be told apart, so everything is kept.
    """
    times = [v.valid_at for s in series for v in s.values if v.status != "missing"]
    if not times:
        return series
    first, last = min(times), max(times)
    for entry in series:
        kept = [v for v in entry.values if first <= v.valid_at <= last]
        entry.outside_run = len(entry.values) - len(kept)
        entry.values = kept
    return series


class ForecastCollector(AlignedCollector):
    """Check the parking-point forecast every thirty minutes on a server-owned thread."""

    thread_name = "WeatherForecastCollector"

    def __init__(
        self,
        ctrl,
        *,
        http=None,
        clock: Callable[[], float] = time.time,
        point: ForecastPoint = PARKING_POINT,
        interval_s: int = INTERVAL_S,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        super().__init__(clock=clock, interval_s=interval_s)
        self._ctrl = ctrl
        self._http = http or requests
        self.point = point
        self.source_id = point.id
        self._timeout_s = timeout_s

    def start(self) -> None:
        if self._thread is not None:
            return
        super().start()
        logger.info(
            "Weather forecast collector started point=%s interval_s=%s",
            self.source_id,
            self.interval_s,
        )

    def collect(self, slot: datetime) -> None:
        try:
            self._collect(slot)
        except Exception as exc:
            # Persistence failed; the next check retries with the then-current run.
            self.archive_errors[self.source_id] = {
                "at": self.now().isoformat(),
                "error": type(exc).__name__,
            }
            logger.exception("Weather forecast archive write failed point=%s", self.source_id)

    def _collect(self, slot: datetime) -> None:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": STOREDQUERY_ID,
            "latlon": self.source_id,
            "timestep": TIMESTEP_MINUTES,
            "parameters": ",".join(PARAMETERS),
            "starttime": iso_z(slot - WINDOW_BEFORE),
            "endtime": iso_z(slot + WINDOW_AFTER),
        }
        retrieval: dict[str, Any] = {
            "kind": "forecast",
            "source_id": self.source_id,
            "intended_at": slot.isoformat(),
            "requested_start": (slot - WINDOW_BEFORE).isoformat(),
            "requested_end": (slot + WINDOW_AFTER).isoformat(),
            "started_at": self.now().isoformat(),
            "request": {
                **{k: params[k] for k in ("storedquery_id", "latlon", "timestep", "parameters")},
                "metadata": METADATA_VERSION,
                "window": WINDOW_LABEL,
            },
        }
        content, series = fetch_reply(
            self._http, params, self._timeout_s, parse_forecast_reply, retrieval, self.now
        )
        if retrieval["status"] != "ok":
            logger.warning(
                "Weather forecast collection failed point=%s error=%s http_status=%s",
                self.source_id,
                retrieval.get("error"),
                retrieval.get("http_status"),
            )
        self._ctrl.record_weather_forecast_retrieval(
            retrieval, content, series, requested=PARAMETERS, meta=PARAMETER_META
        )
        self.archive_errors.pop(self.source_id, None)
