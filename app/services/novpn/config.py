import contextlib
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

NOVPN_CONFIG_PATH = os.path.expanduser("~/.config/novpn/devices.conf")
_DEVICE_CMD = "/usr/local/bin/novpn-device.sh"


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def _parse_bool(val: str) -> bool:
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _format_bool(val: bool) -> str:
    return "true" if bool(val) else "false"


def _normalize_mac(mac: str) -> str:
    mac = (mac or "").strip().lower().replace("-", ":")
    # zero-pad segments and ensure colon-separated
    parts = [p for p in mac.split(":") if p]
    if len(parts) == 6:
        with contextlib.suppress(Exception):
            parts = [f"{int(p, 16):02x}" for p in parts]
        return ":".join(parts)
    return mac


def _parse_device_line(line: str) -> dict[str, object] | None:
    """Parse one device line; returns dict with name, mac, novpn, nodns or None.

    Expected format (free ordering of flags is tolerated):
      /usr/local/bin/novpn-device.sh -name "Foo" -mac aa:bb:... -novpn true -nodns false
    """
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    if _DEVICE_CMD not in line:
        return None
    # Tokenize respecting quoted name
    tokens = re.findall(r'"[^\"]*"|\S+', line)
    if not tokens or tokens[0] != _DEVICE_CMD:
        return None

    name: str | None = None
    mac: str | None = None
    novpn: bool | None = None
    nodns: bool | None = None

    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-name" and i + 1 < len(tokens):
            val = tokens[i + 1]
            name = val.strip('"')
            i += 2
        elif tok == "-mac" and i + 1 < len(tokens):
            mac = tokens[i + 1]
            i += 2
        elif tok == "-novpn" and i + 1 < len(tokens):
            novpn = _parse_bool(tokens[i + 1])
            i += 2
        elif tok == "-nodns" and i + 1 < len(tokens):
            nodns = _parse_bool(tokens[i + 1])
            i += 2
        else:
            i += 1

    if not name or not mac:
        return None
    # Default missing flags to False
    return {
        "name": name,
        "mac": mac.lower(),
        "novpn": bool(novpn),
        "nodns": bool(nodns),
    }


def _restart_novpn_master() -> bool:
    """Restart the novpn-master service via systemctl.

    Returns True if restart succeeded, False otherwise.
    """
    try:
        cmd: list[str] = ["/usr/bin/systemctl", "restart", "novpn-master"]
        # Use sudo if not running as root
        if os.geteuid() != 0:
            cmd.insert(0, "/usr/bin/sudo")

        logger.debug("Restarting novpn-master: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=float(os.getenv("NOVPN_RESTART_TIMEOUT_S", "10")),
        )
        if proc.returncode != 0:
            logger.warning(
                "Failed to restart novpn-master (returncode=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout).strip(),
            )
            return False
        logger.info("Successfully restarted novpn-master.")
        return True
    except Exception as e:
        logger.warning("Exception while restarting novpn-master: %s", e)
        return False


def _rewrite_line_with(line: str, *, novpn: bool | None = None, nodns: bool | None = None) -> str:
    """Rewrite the -novpn/-nodns flags in a device line, preserving other parts."""

    def _replace_flag(s: str, flag: str, value: bool | None) -> str:
        if value is None:
            return s
        pattern = re.compile(rf"({flag}\s+)(\S+)")
        if pattern.search(s):
            return pattern.sub(rf"\1{_format_bool(value)}", s)
        # If flag missing, append at end
        end_nl = "\n" if s.endswith("\n") else ""
        base = s[:-1] if end_nl else s
        return f"{base} {flag} {_format_bool(value)}{end_nl}"

    out = _replace_flag(line, "-novpn", novpn)
    out = _replace_flag(out, "-nodns", nodns)
    return out


def _rewrite_line_meta(line: str, *, name: str | None = None, mac: str | None = None) -> str:
    """Rewrite the -name and/or -mac tokens, preserving other parts."""

    def _replace_token(s: str, flag: str, value: str | None, quoted: bool = False) -> str:
        if value is None:
            return s
        val = value
        if quoted:
            # strip quotes within and wrap again
            val = str(val).replace('"', "")
            repl = f'"{val}"'
        else:
            repl = str(val)
        pattern = re.compile(rf"({flag}\s+)(\"[^\"]*\"|\S+)")
        if pattern.search(s):
            # Use a function replacement to avoid backreference ambiguity like \14 when repl starts with a digit
            return pattern.sub(lambda m: m.group(1) + repl, s)
        # If flag missing, append
        end_nl = "\n" if s.endswith("\n") else ""
        base = s[:-1] if end_nl else s
        sep = "" if base.endswith(" ") else " "
        return f"{base}{sep}{flag} {repl}{end_nl}"

    out = _replace_token(line, "-name", name, quoted=True)
    out = _replace_token(out, "-mac", mac, quoted=False)
    return out


def list_devices(path: str = NOVPN_CONFIG_PATH) -> list[dict[str, object]]:
    """Return devices from config file; missing file yields empty list."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    devices: list[dict[str, object]] = []
    for ln in lines:
        d = _parse_device_line(ln)
        if d:
            devices.append(d)
    return devices


def update_device_flags(
    mac: str, *, novpn: bool | None = None, nodns: bool | None = None, path: str = NOVPN_CONFIG_PATH
) -> tuple[bool, dict[str, object] | None]:
    """Update -novpn/-nodns for the device (by MAC). Returns (ok, updated_device).

    Preserves comments and unrelated lines. Creates the file if missing.
    """
    mac_norm = _normalize_mac(mac)
    _ensure_parent_dir(path)

    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    found = False
    new_lines: list[str] = []
    for ln in lines:
        d = _parse_device_line(ln)
        if d and d.get("mac") == mac_norm:
            found = True
            ln = _rewrite_line_with(ln, novpn=novpn, nodns=nodns)
        new_lines.append(ln)

    if not found:
        return False, None

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, path)

    # Restart novpn-master to apply changes
    if not _restart_novpn_master():
        logger.warning("Warning: novpn-master restart failed after updating device flags.")

    # Return updated snapshot
    updated = None
    for ln in new_lines:
        d = _parse_device_line(ln)
        if d and d.get("mac") == mac_norm:
            updated = d
            break
    return True, updated


def add_device(
    name: str, mac: str, *, novpn: bool = False, nodns: bool = False, path: str = NOVPN_CONFIG_PATH
) -> tuple[bool, dict[str, object] | None]:
    """Append a new device entry to the config file.

    Returns (ok, device). On failure, returns (False, None).
    """
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Name is required.")
    # Disallow quotes to keep a simple line format
    if '"' in nm:
        nm = nm.replace('"', "")

    mac_norm = _normalize_mac(mac)
    if not mac_norm or not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", mac_norm):
        raise ValueError("Invalid MAC address.")

    _ensure_parent_dir(path)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    # Prevent duplicate by MAC
    for ln in lines:
        d = _parse_device_line(ln)
        if d and d.get("mac") == mac_norm:
            raise ValueError("A device with the same MAC already exists.")

    # Compose line
    line = (
        f'{_DEVICE_CMD} -name "{nm}" -mac {mac_norm} '
        f"-novpn {_format_bool(novpn)} -nodns {_format_bool(nodns)}\n"
    )

    new_lines = list(lines)
    # Ensure file ends with a newline before appending (cosmetic)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    new_lines.append(line)

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, path)

    if not _restart_novpn_master():
        logger.warning("Warning: novpn-master restart failed after adding device.")

    device = {
        "name": nm,
        "mac": mac_norm,
        "novpn": bool(novpn),
        "nodns": bool(nodns),
    }
    return True, device


def update_device_meta(
    original_mac: str,
    *,
    name: str | None = None,
    new_mac: str | None = None,
    path: str = NOVPN_CONFIG_PATH,
) -> tuple[bool, dict[str, object] | None]:
    """Update device name and/or MAC, preserving flags. Returns (ok, updated_device).

    - If new_mac duplicates another device, raises ValueError.
    - If device not found, returns (False, None).
    """
    om = _normalize_mac(original_mac)
    nm_mac = _normalize_mac(new_mac) if new_mac else None
    nm_name = None if name is None else str(name).replace('"', "")
    if nm_name is not None and not nm_name.strip():
        raise ValueError("Name cannot be empty.")
    if nm_mac is not None and not re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", nm_mac):
        raise ValueError("Invalid MAC address.")

    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False, None

    # Duplicate MAC check (if changing MAC)
    if nm_mac:
        for ln in lines:
            d = _parse_device_line(ln)
            if not d:
                continue
            if d.get("mac") == nm_mac and d.get("mac") != om:
                raise ValueError("A device with the same MAC already exists.")

    found = False
    new_lines: list[str] = []
    for ln in lines:
        d = _parse_device_line(ln)
        if d and d.get("mac") == om:
            found = True
            ln = _rewrite_line_meta(ln, name=nm_name, mac=nm_mac)
        new_lines.append(ln)

    if not found:
        return False, None

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, path)

    if not _restart_novpn_master():
        logger.warning("Warning: novpn-master restart failed after editing device.")

    # Return updated snapshot
    updated = None
    for ln in new_lines:
        d = _parse_device_line(ln)
        if d and (d.get("mac") == (nm_mac or om)):
            updated = d
            break
    return True, updated


def delete_device(
    mac: str, *, path: str = NOVPN_CONFIG_PATH
) -> tuple[bool, dict[str, object] | None]:
    """Delete a device line by MAC. Returns (ok, removed_device).

    If device not found, returns (False, None).
    """
    mac_norm = _normalize_mac(mac)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False, None

    removed: dict[str, object] | None = None
    new_lines: list[str] = []
    for ln in lines:
        d = _parse_device_line(ln)
        if d and d.get("mac") == mac_norm:
            removed = d
            # skip this line (delete)
            continue
        new_lines.append(ln)

    if removed is None:
        return False, None

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, path)

    if not _restart_novpn_master():
        logger.warning("Warning: novpn-master restart failed after deleting device.")

    return True, removed
