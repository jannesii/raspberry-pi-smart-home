from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


DEFAULT_ADGUARD_LEASES = "/opt/AdGuardHome/data/leases.json"
DEFAULT_STATIC_CACHE = "/tmp/static_dhcp_leases.json"


def _read_json(path: str) -> object | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.debug("leases: file not found: %s", path)
        return None
    except PermissionError as e:
        logger.debug("leases: permission denied reading %s: %s", path, e)
        return None
    except Exception as e:
        logger.warning("leases: failed to parse %s: %s", path, e)
        return None


def read_static_leases(
    *,
    leases_path: str | None = None,
    cache_path: str | None = None,
) -> list[dict[str, str]]:
    """Return static DHCP leases from AdGuard Home leases.json.

    Tries `leases_path` (or env LEASES_FILE, or DEFAULT_ADGUARD_LEASES) first.
    If not readable, falls back to `cache_path` (or env STATIC_LEASES_CACHE or DEFAULT_STATIC_CACHE).

    Output list entries have keys: ip, mac, hostname.
    """
    primary = (leases_path or os.getenv("LEASES_FILE") or DEFAULT_ADGUARD_LEASES).strip()
    fallback = (cache_path or os.getenv("STATIC_LEASES_CACHE") or DEFAULT_STATIC_CACHE).strip()

    data = _read_json(primary)
    if data is None and fallback:
        data = _read_json(fallback)

    out: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return out
    items = data.get("leases")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            is_static = bool(item.get("static", False))
            if not is_static:
                continue
            mac = str(item.get("mac", "")).strip().lower()
            if not mac or mac in {"00:00:00:00:00:00", "00-00-00-00-00-00"}:
                continue
            ip = str(item.get("ip", "")).strip()
            host = str(item.get("hostname", "")).strip()
            out.append(
                {
                    "ip": ip,
                    "mac": mac,
                    "hostname": host,
                }
            )
        except Exception:
            continue
    # Deduplicate by MAC (keep first)
    seen = set()
    dedup: list[dict[str, str]] = []
    for e in out:
        m = e["mac"]
        if m in seen:
            continue
        seen.add(m)
        dedup.append(e)
    return dedup
