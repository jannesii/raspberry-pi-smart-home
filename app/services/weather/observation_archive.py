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
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

from .weather_service import FMI_WFS_URL, PELMAA_ID, RENKO_ID, STOREDQUERY_ID

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

_NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "om": "http://www.opengis.net/om/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "wml2": "http://www.opengis.net/waterml/2.0",
    "target": "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1",
}
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


class MalformedReply(ValueError):
    """The reply is not a parsable WFS observation collection."""


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


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    loc = obs.find(".//target:Location", _NS)
    if loc is not None:
        ident = loc.find("gml:identifier", _NS)
        if ident is not None and ident.text:
            info["fmisid"] = ident.text.strip()
        for name in loc.findall("gml:name", _NS):
            kind = name.attrib.get("codeSpace", "").rsplit("/", 1)[-1]
            if kind and name.text:
                info[kind] = name.text.strip()
        region = loc.find("target:region", _NS)
        if region is not None and region.text:
            info["region"] = region.text.strip()
    pos = obs.find(".//gml:Point/gml:pos", _NS)
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
    if root.tag != f"{{{_NS['wfs']}}}FeatureCollection":
        raise MalformedReply(f"Unexpected root element {root.tag!r}")
    series: list[Series] = []
    for member in root.findall("wfs:member", _NS):
        obs = next(iter(member), None)
        if obs is None:
            continue
        prop = obs.find("om:observedProperty", _NS)
        href = prop.attrib.get(_XLINK_HREF) if prop is not None else None
        param = None
        if href and "param=" in href:
            param = href.split("param=", 1)[1].split("&", 1)[0].strip() or None
        if param is None:
            gml_id = obs.find(".//wml2:MeasurementTimeseries", _NS)
            raw_id = gml_id.attrib.get(f"{{{_NS['gml']}}}id", "") if gml_id is not None else ""
            param = raw_id.rsplit("-", 1)[-1] or None
        if param is None:
            logger.warning("FMI observation member without a parameter identity; skipped")
            continue
        location = _location(obs)
        entry = Series(location.get("fmisid"), param, href, location)
        for tvp in obs.findall(".//wml2:MeasurementTVP", _NS):
            t_el, v_el = tvp.find("wml2:time", _NS), tvp.find("wml2:value", _NS)
            time_text = (t_el.text or "").strip() if t_el is not None else ""
            value_text = (v_el.text or "").strip() if v_el is not None else ""
            reading = _reading(param, time_text, value_text)
            if reading is None:
                entry.skipped += 1
            else:
                entry.readings.append(reading)
        series.append(entry)
    return series


def fetch_reply(http, params, timeout_s, parse, retrieval: dict, now) -> tuple:
    """Fetch and parse one FMI reply, recording the outcome on ``retrieval``.

    Returns ``(content, parsed)`` for a successful reply and ``(None, None)``
    otherwise; failed or malformed replies keep no content.
    """
    content = parsed = None
    try:
        response = http.get(FMI_WFS_URL, params=params, timeout=timeout_s)
        retrieval["http_status"] = response.status_code
        response.raise_for_status()
        content = response.content or b""
        parsed = parse(content)
        retrieval["status"] = "ok"
    except requests.Timeout:
        retrieval.update(status="failed", error="timeout")
    except requests.HTTPError:
        retrieval.update(status="failed", error="http_error")
    except requests.RequestException as exc:
        retrieval.update(status="failed", error=f"request_failed:{type(exc).__name__}")
    except MalformedReply:
        retrieval.update(status="failed", error="malformed_reply")
    retrieval["retrieved_at"] = now().isoformat()
    if retrieval["status"] != "ok":
        return None, None
    return content, parsed


class AlignedCollector:
    """Run ``collect`` once per aligned interval slot on a server-owned thread."""

    thread_name = "WeatherCollector"

    def __init__(self, *, clock: Callable[[], float], interval_s: int) -> None:
        self._clock = clock
        self.interval_s = int(interval_s)
        self._last_slot: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.archive_errors: dict[str, dict] = {}

    def collect(self, slot: datetime) -> None:
        raise NotImplementedError

    def run_pending(self) -> bool:
        """Collect once per aligned slot. Returns True when collected."""
        now = self._clock()
        slot = now - (now % self.interval_s)
        if self._last_slot is not None and slot <= self._last_slot:
            return False
        self._last_slot = slot
        self.collect(datetime.fromtimestamp(slot, UTC))
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self.thread_name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def now(self) -> datetime:
        """Current time on the collector's clock (controllable in tests)."""
        return datetime.fromtimestamp(self._clock(), UTC)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:
                logger.exception("%s cycle failed", self.thread_name)
            now = self._clock()
            wait = self.interval_s - (now % self.interval_s)
            self._stop.wait(max(1.0, min(wait, self.interval_s)))


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
            "starttime": _iso_z(start),
            "endtime": _iso_z(slot),
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
