from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)


FMI_WFS_URL = "https://opendata.fmi.fi/wfs"
STOREDQUERY_ID = "fmi::observations::weather::timevaluepair"
PELMAA_ID = 101486
RENKO_ID = 137188

PARAMETERS = [
    "t2m",  # 2m temperature
    "ws_10min",  # 10min wind speed
    "rh",  # relative humidity
    "n_man",  # manual cloudiness
]

NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "om": "http://www.opengis.net/om/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "wml2": "http://www.opengis.net/waterml/2.0",
}


@dataclass
class WeatherValue:
    value: float
    time: str


@dataclass
class StationInfo:
    station_name: str | None
    fmisid: str | None
    lat: float | None
    lon: float | None


@dataclass
class WeatherData:
    station_name: str | None
    fmisid: str | None
    lat: float | None
    lon: float | None
    t2m: WeatherValue | None
    ws_10min: WeatherValue | None
    rh: WeatherValue | None
    n_man: WeatherValue | None


class WeatherService:
    def __init__(
        self,
        fmisid: str | int = PELMAA_ID,
        *,
        cache_ttl_s: int = 120,
        lookback_hours: int = 2,
        timestep_minutes: int = 10,
        timeout_s: int = 30,
    ) -> None:
        logger.debug(
            "WeatherService.__init__ fmisid=%s cache_ttl_s=%s lookback_hours=%s "
            "timestep_minutes=%s timeout_s=%s",
            fmisid,
            cache_ttl_s,
            lookback_hours,
            timestep_minutes,
            timeout_s,
        )
        self._fmisid = str(fmisid)
        self._cache_ttl_s = int(cache_ttl_s)
        self._lookback_hours = int(lookback_hours)
        self._timestep_minutes = int(timestep_minutes)
        self._timeout_s = int(timeout_s)

        self._cache_time: datetime | None = None
        self._values: dict[str, WeatherValue] = {}
        self._station: StationInfo | None = None

    @property
    def t2m(self) -> WeatherValue | None:
        self._ensure_cache()
        return self._values.get("t2m")

    @property
    def ws_10min(self) -> WeatherValue | None:
        self._ensure_cache()
        return self._values.get("ws_10min")

    @property
    def rh(self) -> WeatherValue | None:
        self._ensure_cache()
        return self._values.get("rh")

    @property
    def n_man(self) -> WeatherValue | None:
        self._ensure_cache()
        return self._values.get("n_man")

    def get_latest(self) -> WeatherData:
        """Return the latest cached weather data."""
        logger.debug("get_latest called")
        self._ensure_cache()
        station = self._station or StationInfo(
            station_name=None,
            fmisid=self._fmisid,
            lat=None,
            lon=None,
        )
        data = WeatherData(
            station_name=station.station_name,
            fmisid=station.fmisid or self._fmisid,
            lat=station.lat,
            lon=station.lon,
            t2m=self.t2m,
            ws_10min=self.ws_10min,
            rh=self.rh,
            n_man=self.n_man,
        )
        logger.debug(
            "get_latest station=%s fmisid=%s fields=%s",
            station.station_name,
            station.fmisid,
            sorted(self._values.keys()),
        )
        return data

    def _ensure_cache(self) -> None:
        now = datetime.now(UTC)
        logger.debug(
            "_ensure_cache now=%s cache_time=%s ttl_s=%s",
            now.isoformat(),
            self._cache_time.isoformat() if self._cache_time else None,
            self._cache_ttl_s,
        )
        if self._cache_time is None:
            logger.debug("Cache empty; refreshing")
            self._refresh_cache()
            return
        age_s = (now - self._cache_time).total_seconds()
        if age_s >= self._cache_ttl_s:
            logger.debug("Cache expired (age_s=%s); refreshing", age_s)
            self._refresh_cache()
            return

    def _refresh_cache(self) -> None:
        logger.debug(
            "_refresh_cache starting fmisid=%s lookback_hours=%s timestep_minutes=%s",
            self._fmisid,
            self._lookback_hours,
            self._timestep_minutes,
        )
        try:
            xml_bytes = self._fetch_fmi()
        except requests.RequestException as exc:
            logger.exception("FMI fetch failed; keeping existing cache: %s", exc)
            return

        if not xml_bytes:
            logger.debug("FMI response empty; keeping existing cache")
            return

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            logger.exception("Failed to parse FMI XML; keeping existing cache: %s", exc)
            return

        results: dict[str, WeatherValue] = {}
        station: StationInfo | None = None
        member_count = 0

        for member in root.findall("wfs:member", NS):
            member_count += 1
            try:
                obs = next(iter(member)) if len(member) else None
                if obs is None:
                    continue

                observed = obs.find("om:observedProperty", NS)
                if observed is None:
                    continue

                href = observed.attrib.get("{http://www.w3.org/1999/xlink}href", "")
                param = self._parse_param_from_observed_property(href)
                if not param or param not in PARAMETERS:
                    continue

                if station is None:
                    station = self._parse_station_info(obs)

                latest = self._parse_latest_valid_tvp(obs)
                if latest is None:
                    continue
                time_iso, value = latest
                results[param] = WeatherValue(value=value, time=time_iso)
            except Exception as exc:
                logger.exception("Failed parsing FMI member: %s", exc)
                continue

        if station is None:
            station = StationInfo(
                station_name=None,
                fmisid=self._fmisid,
                lat=None,
                lon=None,
            )

        self._station = station
        self._values = results
        self._cache_time = datetime.now(UTC)
        logger.debug(
            "_refresh_cache completed members=%s values=%s station=%s cache_time=%s",
            member_count,
            sorted(results.keys()),
            station,
            self._cache_time.isoformat(),
        )

    def _fetch_fmi(self) -> bytes:
        now = datetime.now(UTC)
        start = now - timedelta(hours=self._lookback_hours)

        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": STOREDQUERY_ID,
            "fmisid": self._fmisid,
            "starttime": self._iso_z(start),
            "endtime": self._iso_z(now),
            "timestep": str(self._timestep_minutes),
            "parameters": ",".join(PARAMETERS),
        }
        url = f"{FMI_WFS_URL}?{urlencode(params)}"
        logger.debug("Fetching FMI data: %s", url)
        try:
            resp = requests.get(url, timeout=self._timeout_s)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("FMI request failed: %s", exc)
            raise
        logger.debug(
            "FMI response received status=%s bytes=%s",
            resp.status_code,
            len(resp.content) if resp.content else 0,
        )
        return resp.content

    @staticmethod
    def _iso_z(dt: datetime) -> str:
        dt_utc = dt.astimezone(UTC)
        return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_param_from_observed_property(href: str) -> str | None:
        if "param=" not in href:
            return None
        part = href.split("param=", 1)[1]
        return part.split("&", 1)[0].strip() if part else None

    @staticmethod
    def _parse_station_info(obs_elem: ET.Element) -> StationInfo:
        station_name: str | None = None
        fmisid: str | None = None
        lat: float | None = None
        lon: float | None = None

        p_name = obs_elem.find(".//gml:Point/gml:name", NS)
        if p_name is not None and p_name.text:
            station_name = p_name.text.strip()

        ident = obs_elem.find(".//gml:identifier", NS)
        if ident is not None and ident.text:
            fmisid = ident.text.strip()

        pos = obs_elem.find(".//gml:Point/gml:pos", NS)
        if pos is not None and pos.text:
            latlon = pos.text.strip().split()
            if len(latlon) == 2:
                try:
                    lat = float(latlon[0])
                    lon = float(latlon[1])
                except ValueError:
                    lat = None
                    lon = None

        return StationInfo(
            station_name=station_name,
            fmisid=fmisid,
            lat=lat,
            lon=lon,
        )

    @staticmethod
    def _parse_latest_valid_tvp(
        obs_elem: ET.Element,
    ) -> tuple[str, float] | None:
        latest: tuple[datetime, float] | None = None

        for tvp in obs_elem.findall(".//wml2:MeasurementTVP", NS):
            t_el = tvp.find("wml2:time", NS)
            v_el = tvp.find("wml2:value", NS)
            if t_el is None or v_el is None or not t_el.text or not v_el.text:
                continue

            try:
                t = datetime.fromisoformat(t_el.text.strip().replace("Z", "+00:00"))
                v = float(v_el.text.strip())
            except Exception:
                continue

            if not math.isfinite(v):
                continue

            if latest is None or t > latest[0]:
                latest = (t, v)

        if latest is None:
            return None
        return latest[0].isoformat(), latest[1]
