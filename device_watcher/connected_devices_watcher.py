"""Connected devices watcher for AdGuard Home DHCP.

Detects new devices appearing in the DHCP lease list and publishes
notifications via a pluggable notifier (e.g. Socket.IO broadcast) and/or
an optional webhook.

This module is written in the same service style as the rest of the
codebase: logging via the app logger, type hints, and an explicit class
that can be started from the app's bootstrap if desired.
"""

from __future__ import annotations

import os
import subprocess
import time
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set, Tuple, Any
import socket
import dotenv

dotenv.load_dotenv(
    "/etc/device_watcher.env"
)  # take environment variables from .env if available

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)

logger = logging.getLogger(__name__)


# Optional: try to import PyYAML to read AdGuardHome.yaml for static leases
try:  # pragma: no cover - best-effort optional dependency
    import yaml  # type: ignore

    HAVE_YAML = True
except Exception:  # pragma: no cover
    HAVE_YAML = False


def _load_static_from_yaml(cfg_path: str) -> Tuple[Set[str], Set[str]]:
    """Return (static_macs, static_ips) gathered from AdGuard YAML config.

    MACs are normalized to lowercase.
    """
    static_macs: Set[str] = set()
    static_ips: Set[str] = set()
    if not HAVE_YAML:
        logger.warning("PyYAML not installed; static leases from YAML will not be read")
        return static_macs, static_ips
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        dhcp = data.get("clients", {})
        stat = dhcp.get("persistent", [])
        for entry in stat:
            mac = entry.get("ids", [""])[0].strip().lower()
            if mac:
                static_macs.add(mac)
    except Exception as e:
        logger.warning("Failed to parse static leases from %s: %s", cfg_path, e)
    return static_macs, static_ips


def _parse_json_leases(leases_path: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Best-effort JSON leases parser (AdGuard variants).

    Attempts to parse common shapes found in AdGuard Home data directories.
    Returns None if the file is not JSON or an error occurs.
    """
    try:
        with open(leases_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.exception("Failed to read JSON leases from %s: %s", leases_path, e)
        return None

    out: Dict[str, Dict[str, Any]] = {}
    try:
        if (
            isinstance(data, dict)
            and "leases" in data
            and isinstance(data["leases"], list)
        ):
            for item in data["leases"]:
                ip = str(item.get("ip", "")).strip()
                mac = str(item.get("mac", "")).strip().lower()
                host = str(item.get("hostname", "")).strip()
                is_static = bool(item.get("static", False))
                expires = str(item.get("expires", ""))
                if ip and mac:
                    out[ip] = {
                        "mac": mac,
                        "hostname": host,
                        "expiry": expires,
                        "static": is_static,
                    }
            return out
    except Exception as e:
        logger.exception("JSON leases parsing error: %s", e)
    return None


def _host_reverse(ip: str, timeout_s: float = 2.0) -> str:
    """Reverse-lookup an IP.

    First tries local resolver via `dig @127.0.0.1` and falls back to
    the system resolver (`socket.gethostbyaddr`). Returns a short
    hostname without a trailing dot, or empty string if not resolvable.
    """
    try:
        cmd = ["dig", "-x", ip, "@127.0.0.1", "+short"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        out = (proc.stdout or "").strip()
        if out:
            host = out.splitlines()[0].strip().rstrip(".")
            return host
    except Exception:
        pass
    try:
        host, aliases, _ = socket.gethostbyaddr(ip)
        return (host or "").strip().rstrip(".")
    except Exception:
        return ""


def _is_placeholder_mac(mac: str) -> bool:
    m = mac.lower()
    return m in {"00:00:00:00:00:00", "00-00-00-00-00-00"}


def _neighbor_mac_for_ip(ip: str, timeout_s: float = 2.0) -> str:
    """Try to resolve a MAC for an on-link IPv4 via neighbor table.

    Parses output of `ip neigh show to <ip>` and returns lowercased MAC or ''.
    """
    try:
        proc = subprocess.run(
            ["ip", "-4", "neigh", "show", "to", ip],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        out = (proc.stdout or "").strip()
        # Example: "192.168.1.10 dev eth0 lladdr 00:11:22:33:44:55 REACHABLE"
        m = re.search(r"lladdr\s+([0-9A-Fa-f:]{17})", out)
        return m.group(1).lower() if m else ""
    except Exception:
        return ""


def _load_state(path: str) -> Dict[str, dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(path: str, state: Dict[str, dict]) -> None:
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("Failed to save presence watcher state: %s", e)


def _parse_nmap_ping_sweep(output: str) -> List[Dict[str, str]]:
    """Parse `nmap -sn` output into a list of hosts.

    Returns a list of dicts with keys: ip, mac (optional), vendor (optional).
    Tolerates both "report for <ip>" and "report for <host> (<ip>)" formats.
    """
    hosts: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    # Regexes for common patterns
    # General form: "Nmap scan report for <target>" where target may be
    # an IP (v4/v6) or a name like "host (ip)".
    header_re = re.compile(r"^Nmap scan report for\s+(.+)$")
    mac_re = re.compile(r"^MAC Address:\s*([0-9A-Fa-f:]{17})(?:\s*\(([^\)]+)\))?")

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        m_hdr = header_re.match(line)
        if m_hdr:
            # Flush any previous host entry
            if current:
                hosts.append(current)
            target = m_hdr.group(1).strip()
            # If target contains parentheses, take inner content (likely IP)
            if target.endswith(")") and "(" in target:
                ip = target[target.rfind("(") + 1 : -1].strip()
            else:
                ip = target
            current = {"ip": ip}
            continue
        m_mac = mac_re.match(line)
        if m_mac and current is not None:
            current["mac"] = m_mac.group(1).lower()
            if m_mac.group(2):
                current["vendor"] = m_mac.group(2)
            continue

    if current:
        hosts.append(current)
    return hosts


class ConnectedDevicesWatcher:
    """Poll AdGuard Home DHCP leases and detect newly observed devices.

    - Emits notifications via `notify(event, payload)` if provided (e.g., to Socket.IO views)
    - Optionally posts to a simple JSON webhook `{ "text": "..." }`
    - Persists a small JSON state of seen devices to avoid duplicate alerts
    """

    def __init__(
        self,
        *,
        adguard_config_file: str = None,
        leases_file: str = None,
        state_file: Optional[str] = None,
        interval_s: int = 15,
        webhook_url: Optional[str] = None,
        notify: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        nmap_subnets: Optional[List[str]] = None,
        nmap_path: str = "nmap",
        nmap_use_sudo: bool = False,
        nmap6_subnets: Optional[List[str]] = None,
        nmap_interval_s: int = 60,
        ipv6_neighbor_watch: bool = True,
        rdns_enabled: bool = True,
        rdns_ttl_s: int = 300,
        ignore_placeholder_dhcp: bool = True,
        export_static_leases_path: Optional[str] = None,
    ) -> None:
        # Default to tmp to avoid permission issues; can be overridden via env
        self.state_file = state_file or "/tmp/device_watcher_state.json"
        self.interval_s = max(1, int(interval_s))
        self.webhook_url = webhook_url or None
        self.notify = notify

        # Derived/loaded state
        self._leases_path = leases_file
        self._cfg_path = adguard_config_file
        self._static_macs: Set[str] = set()
        self._static_ips: Set[str] = set()
        self._seen: Dict[str, dict] = {}
        # Nmap config
        self._nmap_subnets: List[str] = list(nmap_subnets or [])
        self._nmap_path: str = nmap_path
        self._nmap_use_sudo: bool = bool(nmap_use_sudo)
        self._nmap6_subnets: List[str] = list(nmap6_subnets or [])
        self._nmap_interval_s: int = max(1, int(nmap_interval_s))
        self._last_nmap_run: float = 0.0
        self._ipv6_neighbor_watch: bool = bool(ipv6_neighbor_watch)
        # rDNS cache and settings
        self._rdns_enabled: bool = bool(rdns_enabled)
        self._rdns_ttl_s: float = float(max(0, int(rdns_ttl_s)))
        self._rdns_cache: Dict[str, Tuple[float, str]] = {}
        # DHCP behavior
        self._ignore_placeholder_dhcp: bool = bool(ignore_placeholder_dhcp)
        # Tracks whether state changed during a scan (e.g., hostname/rdns refresh)
        self._updates_made: bool = False
        # Optional export path for static leases (to share with web app)
        self._export_static_path: Optional[str] = export_static_leases_path

    # --- Public API -----------------------------------------------------

    def start(self) -> None:
        """Blocking run loop. Intended to be executed in a background thread."""
        self._bootstrap()
        logger.info(
            "Presence watcher: interval=%ss, leases=%s, nmap_subnets=%s, nmap6_subnets=%s, nmap_interval=%ss, ipv6_neigh=%s, rdns=%s",
            self.interval_s,
            self._leases_path,
            ",".join(self._nmap_subnets) if self._nmap_subnets else "-",
            ",".join(self._nmap6_subnets) if self._nmap6_subnets else "-",
            self._nmap_interval_s,
            self._ipv6_neighbor_watch,
            self._rdns_enabled,
        )
        while True:
            try:
                new_entries: List[Dict[str, Any]] = []
                # DHCP-based discovery
                new_entries.extend(self.scan_once())
                # Nmap-based discovery of non-DHCP devices (cadenced)
                now = time.time()
                if (self._nmap_subnets or self._nmap6_subnets) and (
                    now - self._last_nmap_run >= self._nmap_interval_s
                ):
                    logger.info(
                        "Triggering nmap sweeps: v4_subnets=%d v6_subnets=%d",
                        len(self._nmap_subnets),
                        len(self._nmap6_subnets),
                    )
                    if self._nmap_subnets:
                        new_entries.extend(self.nmap_scan_once())
                    if self._nmap6_subnets:
                        new_entries.extend(self.nmap6_scan_once())
                    self._last_nmap_run = now
                # IPv6 neighbor-based discovery
                if self._ipv6_neighbor_watch:
                    new_entries.extend(self.ip6_neighbor_scan_once())
                for e in new_entries:
                    msg = (
                        f"[NEW DEVICE/{e.get('source','DHCP').upper()}] IP={e['ip']} MAC={e.get('mac') or 'n/a'} "
                        f"hostname={e.get('hostname') or 'n/a'} rdns={e.get('rdns') or 'n/a'}"
                    )
                    logger.info(msg)
                    self._send_webhook(msg)
                    if self.notify:
                        try:
                            self.notify("presence_new_device", e)
                        except Exception:
                            # Don’t let notify failures break the loop
                            logger.debug(
                                "Presence watcher notify failed", exc_info=True
                            )
                if new_entries or self._updates_made:
                    logger.info(
                        "Saving state: total=%d new=%d updated=%d",
                        len(self._seen),
                        len(new_entries),
                        getattr(self, "_updates_count", 0),
                    )
                    _save_state(self.state_file, self._seen)
            except Exception:
                logger.exception("Presence watcher tick failed")
            time.sleep(self.interval_s)

    def scan_once(self) -> List[Dict[str, Any]]:
        """Poll the leases file once and return a list of newly seen devices.

        Each item is a dict with keys: ip, mac, hostname, rdns, first_seen
        """
        # Reset dirty flag for this tick
        self._updates_made = False
        self._updates_count = 0
        leases = self._load_leases()
        # Export static leases snapshot if configured
        try:
            self._maybe_export_static_leases(leases)
        except Exception:
            logger.debug("Presence watcher: export static leases failed", exc_info=True)
        new_entries: List[Dict[str, Any]] = []
        for ip, info in leases.items():
            mac = info.get("mac", "").lower()
            if not mac:
                # Try to resolve via neighbor table
                mac_guess = _neighbor_mac_for_ip(ip)
                if mac_guess:
                    mac = mac_guess
                else:
                    continue
            if self._ignore_placeholder_dhcp and _is_placeholder_mac(mac):
                mac_guess = _neighbor_mac_for_ip(ip)
                if mac_guess:
                    mac = mac_guess
                else:
                    # Ignore placeholder MAC until we learn a real one
                    continue
            hostname = info.get("hostname") or ""
            is_static_json = bool(info.get("static", False))
            key = f"{mac}|{ip}"
            existing = key in self._seen

            # If we've seen this device before, refresh hostname/rdns when static/renamed
            if existing:
                seen = self._seen[key]
                updated = False
                # Update hostname if it's changed and non-empty
                if hostname and hostname != (seen.get("hostname") or ""):
                    seen["hostname"] = hostname
                    updated = True
                # If the lease is now static, refresh rDNS (it may be more stable now)
                if is_static_json:
                    new_rdns = self._rdns_lookup(ip, force=True)
                    if new_rdns and new_rdns != (seen.get("rdns") or ""):
                        seen["rdns"] = new_rdns
                        updated = True
                    seen["static"] = True
                if updated:
                    logger.info(
                        "Presence watcher: updated device info for %s -> hostname=%s rdns=%s",
                        key,
                        seen.get("hostname"),
                        seen.get("rdns"),
                    )
                    self._updates_made = True
                    self._updates_count += 1
                continue

            # Consider it "known static" if MAC is in static list from YAML
            is_static_yaml = mac in self._static_macs
            if is_static_yaml:
                logger.debug("Presence watcher: skipping static device %s (YAML)", key)
                continue
            rdns = self._rdns_lookup(ip)
            entry = {
                "ip": ip,
                "mac": mac,
                "hostname": hostname,
                "rdns": rdns,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "source": "dhcp",
                "static": is_static_json,
            }
            self._seen[key] = entry
            new_entries.append(entry)
        if new_entries or self._updates_count:
            logger.info(
                "DHCP scan: leases=%d new=%d updated=%d",
                len(leases),
                len(new_entries),
                self._updates_count,
            )
        return new_entries

    def nmap_scan_once(self) -> List[Dict[str, Any]]:
        """Run `nmap -sn` on configured subnets and return non-DHCP devices.

        - Parses live hosts, filters out those present in DHCP leases and known static MACs.
        - Emits entries keyed by mac|ip (if no MAC, uses "?|ip").
        """
        try:
            discovered = self._run_nmap_ping_sweep(self._nmap_subnets)
        except Exception:
            logger.exception("Nmap sweep failed")
            return []

        leases = self._load_leases()
        dhcp_ips: Set[str] = set(leases.keys())
        dhcp_macs: Set[str] = {
            v.get("mac", "").lower() for v in leases.values() if v.get("mac")
        }

        new_entries: List[Dict[str, Any]] = []
        for host in discovered:
            ip = host.get("ip", "").strip()
            mac = host.get("mac", "").strip().lower()
            vendor = host.get("vendor", "")
            if not ip:
                continue
            # Skip static known MACs
            if mac and mac in self._static_macs:
                continue
            # Consider "non-DHCP" if neither IP nor MAC appear in DHCP leases
            if (ip in dhcp_ips) or (mac and mac in dhcp_macs):
                continue

            key_mac = mac or "?"
            key = f"{key_mac}|{ip}"
            if key in self._seen:
                continue

            rdns = self._rdns_lookup(ip)
            entry = {
                "ip": ip,
                "mac": mac or "",
                "hostname": "",
                "rdns": rdns,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "vendor": vendor,
                "source": "nmap",
            }
            self._seen[key] = entry
            new_entries.append(entry)
        logger.info(
            "nmap v4 sweep: discovered=%d new=%d", len(discovered), len(new_entries)
        )
        logger.info(
            "nmap v6 sweep: discovered=%d new=%d", len(discovered), len(new_entries)
        )
        return new_entries

    # --- Internals ------------------------------------------------------

    def _bootstrap(self) -> None:
        # Load static leases if YAML exists
        if self._cfg_path:
            sm, si = _load_static_from_yaml(self._cfg_path)

            self._static_macs |= sm
            if self._static_macs or self._static_ips:
                logger.info(
                    "Presence watcher: loaded static leases: %d MACs",
                    len(self._static_macs),
                )
            else:
                logger.warning(
                    "Presence watcher: no static leases found in %s", self._cfg_path
                )
        else:
            logger.debug(
                "Presence watcher: AdGuard YAML not found; static MAC/IP matching disabled"
            )

        # Load persisted state
        self._seen = _load_state(self.state_file)
        if self._seen:
            logger.info("Presence watcher: loaded state entries: %d", len(self._seen))
        else:
            logger.info("Presence watcher: no previous state; starting fresh")

    def _load_leases(self) -> Dict[str, Dict[str, str]]:
        # Prefer JSON if parseable; fallback to text format
        if not self._leases_path:
            return {}
        parsed = _parse_json_leases(self._leases_path)
        if parsed is not None:
            return parsed
        # Fallback: empty dict (text format not implemented yet)
        return {}

    def _maybe_export_static_leases(self, leases: Dict[str, Dict[str, Any]]) -> None:
        path = (self._export_static_path or "").strip()
        if not path:
            return
        static_list: List[Dict[str, Any]] = []
        for ip, info in leases.items():
            try:
                if not bool(info.get("static", False)):
                    continue
                mac = str(info.get("mac", "")).strip().lower()
                if not mac or _is_placeholder_mac(mac):
                    continue
                static_list.append(
                    {
                        "ip": ip,
                        "mac": mac,
                        "hostname": str(info.get("hostname", "")).strip(),
                        "static": True,
                    }
                )
            except Exception:
                continue
        # Dedup by MAC
        seen: Set[str] = set()
        dedup: List[Dict[str, Any]] = []
        for e in static_list:
            m = e["mac"]
            if m in seen:
                continue
            seen.add(m)
            dedup.append(e)
        # Write JSON in AdGuard-like format to make readers simple
        to_write = {"version": 1, "leases": dedup}
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_write, f, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def _run_nmap_ping_sweep(self, subnets: List[str]) -> List[Dict[str, str]]:
        """Execute nmap ping sweep on the provided subnets and parse output."""
        all_hosts: List[Dict[str, str]] = []
        for subnet in subnets:
            cmd: List[str] = []
            if self._nmap_use_sudo:
                cmd.append("sudo")
            cmd.extend([self._nmap_path, "-sn", subnet])
            logger.debug("Running nmap: %s", " ".join(cmd))
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=float(os.getenv("NMAP_TIMEOUT_S", "20")),
                )
            except Exception as e:
                logger.warning("Failed to execute nmap for %s: %s", subnet, e)
                continue

            out = proc.stdout or ""
            err = proc.stderr or ""
            if proc.returncode != 0:
                logger.warning(
                    "nmap returned %s for %s: %s", proc.returncode, subnet, err.strip()
                )
            hosts = _parse_nmap_ping_sweep(out)
            all_hosts.extend(hosts)
        return all_hosts

    def nmap6_scan_once(self) -> List[Dict[str, Any]]:
        """Run `nmap -6 -sn` on configured IPv6 subnets and return non-DHCP devices."""
        try:
            discovered = self._run_nmap6_ping_sweep(self._nmap6_subnets)
        except Exception:
            logger.exception("Nmap -6 sweep failed")
            return []

        leases = self._load_leases()
        dhcp_macs: Set[str] = {
            v.get("mac", "").lower() for v in leases.values() if v.get("mac")
        }

        new_entries: List[Dict[str, Any]] = []
        for host in discovered:
            ip = host.get("ip", "").strip()
            mac = host.get("mac", "").strip().lower()
            vendor = host.get("vendor", "")
            if not ip:
                continue
            if mac and mac in self._static_macs:
                continue
            if mac and mac in dhcp_macs:
                continue
            key = f"{mac or '?'}|{ip}"
            if key in self._seen:
                continue
            rdns = self._rdns_lookup(ip)
            entry = {
                "ip": ip,
                "mac": mac or "",
                "hostname": "",
                "rdns": rdns,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "vendor": vendor,
                "source": "nmap6",
            }
            self._seen[key] = entry
            new_entries.append(entry)
        return new_entries

    def _run_nmap6_ping_sweep(self, subnets: List[str]) -> List[Dict[str, str]]:
        """Execute `nmap -6 -sn` on IPv6 subnets and parse output."""
        all_hosts: List[Dict[str, str]] = []
        for subnet in subnets:
            cmd: List[str] = []
            if self._nmap_use_sudo:
                cmd.append("sudo")
            cmd.extend([self._nmap_path, "-6", "-sn", subnet])
            logger.debug("Running nmap6: %s", " ".join(cmd))
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=float(os.getenv("NMAP_TIMEOUT_S", "20")),
                )
            except Exception as e:
                logger.warning("Failed to execute nmap6 for %s: %s", subnet, e)
                continue
            out = proc.stdout or ""
            err = proc.stderr or ""
            if proc.returncode != 0:
                logger.warning(
                    "nmap6 returned %s for %s: %s", proc.returncode, subnet, err.strip()
                )
            hosts = _parse_nmap_ping_sweep(out)
            all_hosts.extend(hosts)
        return all_hosts

    def ip6_neighbor_scan_once(self) -> List[Dict[str, Any]]:
        """Scan IPv6 neighbor table for on-link devices and return new entries."""
        try:
            proc = subprocess.run(
                ["ip", "-6", "neigh", "show"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except Exception as e:
            logger.debug("ip -6 neigh failed: %s", e)
            return []
        out = proc.stdout or ""
        new_entries: List[Dict[str, Any]] = []
        total_lines = 0
        for raw in out.splitlines():
            line = raw.strip()
            if not line:
                continue
            total_lines += 1
            parts = line.split()
            if not parts:
                continue
            ip = parts[0]
            m_ll = re.search(r"lladdr\s+([0-9A-Fa-f:]{17})", line)
            if not m_ll:
                continue
            mac = m_ll.group(1).lower()
            if mac in self._static_macs:
                continue
            key = f"{mac or '?'}|{ip}"
            if key in self._seen:
                continue
            rdns = self._rdns_lookup(ip)
            entry = {
                "ip": ip,
                "mac": mac,
                "hostname": "",
                "rdns": rdns,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "vendor": "",
                "source": "ip6-neigh",
            }
            self._seen[key] = entry
            new_entries.append(entry)
        if new_entries:
            logger.info(
                "IPv6 neighbors: entries=%d new=%d", total_lines, len(new_entries)
            )
        else:
            logger.debug("IPv6 neighbors: entries=%d new=0", total_lines)
        return new_entries

    # rDNS cache ---------------------------------------------------------
    def _rdns_lookup(self, ip: str, force: bool = False) -> str:
        if not self._rdns_enabled:
            return ""
        now = time.time()
        if not force:
            cached = self._rdns_cache.get(ip)
            if cached and cached[0] > now:
                return cached[1]
        name = _host_reverse(ip)
        if self._rdns_ttl_s > 0:
            self._rdns_cache[ip] = (now + self._rdns_ttl_s, name)
        return name

    def _send_webhook(self, msg: str) -> None:
        if not self.webhook_url:
            return
        try:  # use stdlib to avoid extra deps
            import requests

            data = {
                "content": f"@everyone {msg}",
                "username": "Device Watcher",
            }
            response = requests.post(self.webhook_url, json=data)
            response.raise_for_status()
        except Exception as e:
            logger.warning("Presence watcher webhook send failed: %s", e)


def _from_env_defaults() -> ConnectedDevicesWatcher:
    """Build a watcher using single-path environment variables."""
    cfg_file = (os.getenv("ADGUARD_CONFIG_FILE") or "").strip() or None
    leases_file = (os.getenv("LEASES_FILE") or "").strip() or None
    interval = int(os.getenv("INTERVAL_S", "15") or 15)
    state = os.getenv("STATE_FILE")
    webhook = os.getenv("WEBHOOK_URL")
    nmap_subnets_env = (
        os.getenv("NMAP_SUBNETS") or os.getenv("NMAP_SUBNET") or ""
    ).strip()
    nmap_subnets = (
        [s.strip() for s in nmap_subnets_env.split(",") if s.strip()]
        if nmap_subnets_env
        else []
    )
    nmap6_subnets_env = (
        os.getenv("NMAP6_SUBNETS") or os.getenv("NMAP6_SUBNET") or ""
    ).strip()
    nmap6_subnets = (
        [s.strip() for s in nmap6_subnets_env.split(",") if s.strip()]
        if nmap6_subnets_env
        else []
    )
    nmap_interval_s = int(os.getenv("NMAP_INTERVAL_S", "60") or 60)
    nmap_use_sudo = os.getenv("NMAP_USE_SUDO", "0").lower() in {"1", "true", "yes", "y"}
    nmap_path = (os.getenv("NMAP_PATH") or "nmap").strip()
    ipv6_neighbor_watch = os.getenv("IPV6_NEIGHBOR_WATCH", "1").lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    rdns_enabled = os.getenv("RDNS_ENABLED", "1").lower() in {"1", "true", "yes", "y"}
    rdns_ttl_s = int(os.getenv("RDNS_TTL_S", "300") or 300)
    ignore_placeholder_dhcp = os.getenv("IGNORE_PLACEHOLDER_DHCP", "1").lower() in {
        "1",
        "true",
        "yes",
        "y",
    }

    export_static_leases_path = (
        os.getenv("STATIC_LEASES_CACHE") or os.getenv("EXPORT_STATIC_LEASES_JSON") or ""
    ).strip() or None

    return ConnectedDevicesWatcher(
        adguard_config_file=cfg_file,
        leases_file=leases_file,
        state_file=state,
        interval_s=interval,
        webhook_url=webhook,
        nmap_subnets=nmap_subnets,
        nmap_path=nmap_path,
        nmap_use_sudo=nmap_use_sudo,
        nmap6_subnets=nmap6_subnets,
        nmap_interval_s=nmap_interval_s,
        ipv6_neighbor_watch=ipv6_neighbor_watch,
        rdns_enabled=rdns_enabled,
        rdns_ttl_s=rdns_ttl_s,
        ignore_placeholder_dhcp=ignore_placeholder_dhcp,
        export_static_leases_path=export_static_leases_path,
    )


def main() -> None:  # pragma: no cover - manual/CLI usage helper
    """Run watcher in foreground using environment variables for config."""
    watcher = _from_env_defaults()
    watcher.start()


if __name__ == "__main__":  # pragma: no cover
    main()
