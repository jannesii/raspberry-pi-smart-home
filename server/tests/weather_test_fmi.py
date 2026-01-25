#!/usr/bin/env python3
from __future__ import annotations

import sys
import math
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import requests

now = datetime.now(timezone.utc)

FMI_WFS_URL = "https://opendata.fmi.fi/wfs"
STOREDQUERY_ID = "fmi::observations::weather::timevaluepair"

PARAMETERS = [
        "t2m",          # 2m temperature
        "ws_10min",     # 10min wind speed
        "rh",           # relative humidity
        "n_man"         # manual cloudiness
    ]  # add more later once you verify they work


NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "om": "http://www.opengis.net/om/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "wml2": "http://www.opengis.net/waterml/2.0",
}


def parse_param_from_observed_property(href: str) -> str | None:
    # href looks like:
    # https://opendata.fmi.fi/meta?observableProperty=observation&param=t2m&language=eng
    if "param=" not in href:
        return None
    part = href.split("param=", 1)[1]
    return part.split("&", 1)[0].strip() if part else None


def parse_station_info(obs_elem: ET.Element) -> dict[str, str]:
    """
    Extract station name, fmisid, lat/lon if present (same for each parameter obs).
    """
    info: dict[str, str] = {}

    # Station name (gml:Point/gml:name)
    p_name = obs_elem.find(".//gml:Point/gml:name", NS)
    if p_name is not None and p_name.text:
        info["station_name"] = p_name.text.strip()

    # FMISID (gml:identifier where codespace stationcode/fmisid)
    ident = obs_elem.find(".//gml:identifier", NS)
    if ident is not None and ident.text:
        info["fmisid"] = ident.text.strip()

    # Coordinates (gml:pos is "lat lon")
    pos = obs_elem.find(".//gml:Point/gml:pos", NS)
    if pos is not None and pos.text:
        latlon = pos.text.strip().split()
        if len(latlon) == 2:
            info["lat"] = latlon[0]
            info["lon"] = latlon[1]

    return info


def parse_latest_valid_tvp(obs_elem: ET.Element) -> tuple[datetime, float] | None:
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

        # ✅ skip missing values
        if math.isnan(v) or math.isinf(v):
            continue

        if latest is None or t > latest[0]:
            latest = (t, v)

    return latest


def fetch_fmi(place: str) -> bytes:
    from datetime import timedelta, timezone
    from urllib.parse import urlencode
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)

    def iso_z(dt: datetime) -> str:
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": STOREDQUERY_ID,
        "fmisid": 137188,
        "starttime": iso_z(start),
        "endtime": iso_z(now),
        "timestep": "10",
        "parameters": ",".join(PARAMETERS),
    }
    url = f"{FMI_WFS_URL}?{urlencode(params)}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content


def main() -> int:
    place = sys.argv[1] if len(sys.argv) > 1 else "Seinäjoki"
    xml_bytes = fetch_fmi(place)

    root = ET.fromstring(xml_bytes)

    results: dict[str, dict] = {}
    station: dict[str, str] | None = None

    # Each wfs:member contains one omso:PointTimeSeriesObservation for one parameter
    for member in root.findall("wfs:member", NS):
        obs = list(member)[0] if len(member) else None
        if obs is None:
            continue

        observed = obs.find("om:observedProperty", NS)
        if observed is None:
            continue

        href = observed.attrib.get("{http://www.w3.org/1999/xlink}href", "")
        param = parse_param_from_observed_property(href)
        if not param:
            continue

        if station is None:
            station = parse_station_info(obs)

        latest = parse_latest_valid_tvp(obs)
        if latest is None:
            continue

        t, v = latest
        results[param] = {"time": t.isoformat(), "value": v}

    if station:
        print("Station:", station)
    else:
        print("Station: (not found)")

    print("Latest values:")
    for p in PARAMETERS:
        if p in results:
            print(f"  {p:8s} {results[p]['value']:8.2f} @ {results[p]['time']}")
        else:
            print(f"  {p:8s} (missing)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
