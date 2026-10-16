"""NoVPN (network bypass) settings page under /settings."""

from __future__ import annotations

import logging

from flask import render_template, request
from flask_login import login_required

from ...services.dhcp import read_static_leases
from ...services.novpn import (
    add_device as novpn_add_device,
    delete_device as novpn_delete_device,
    list_devices as novpn_list_devices,
    update_device_flags as novpn_update_device_flags,
    update_device_meta as novpn_update_device_meta,
)
from ...utils import (
    flash_error,
    flash_success,
    require_root_admin_or_redirect,
)
from . import web_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route("/settings/network_bypass", methods=["GET", "POST"])
@login_required
def novpn_settings():
    """Per-device VPN/DNS bypass settings backed by ~/.config/novpn/devices.conf.
    Root-Admin only.
    """
    guard = require_root_admin_or_redirect(
        "You do not have permission to change NoVPN settings.", "web.get_settings_page"
    )
    if guard:
        return guard
    try:
        if request.method == "POST":
            if "add_device" in request.form:
                # Manual add: require both name and MAC
                name = (request.form.get("new_name") or "").strip()
                mac = (request.form.get("new_mac") or "").strip()
                try:
                    if not name or not mac:
                        raise ValueError("Please fill both name and MAC.")
                    ok, _ = novpn_add_device(name=name, mac=mac, novpn=False, nodns=True)
                    if ok:
                        flash_success("Device added.")
                    else:
                        flash_error("Failed to add device.")
                except ValueError as ve:
                    flash_error(str(ve))
                except Exception as e:
                    flash_error(f"Failed to add device: {e}")
            elif "edit_device" in request.form:
                # Edit existing (or treat as add if not found)
                name = (request.form.get("new_name") or "").strip()
                mac_new = (request.form.get("new_mac") or "").strip()
                mac_orig = (request.form.get("edit_original_mac") or "").strip()
                logger.debug(
                    "Editing device: orig_mac=%s, new_name=%s, new_mac=%s", mac_orig, name, mac_new
                )
                try:
                    if not mac_orig:
                        raise ValueError("Original device MAC is missing.")
                    # At least one change should be provided
                    if not name and not mac_new:
                        raise ValueError("Provide a name and/or MAC to update.")
                    ok, _updated = novpn_update_device_meta(
                        mac_orig, name=name or None, new_mac=mac_new or None
                    )
                    if not ok:
                        # Fallback: if not found, treat as add (use provided fields)
                        nm = name or mac_new
                        mm = mac_new or mac_orig
                        if not nm or not mm:
                            raise ValueError(
                                "Please fill both name and MAC when creating a new device."
                            )
                        ok, _ = novpn_add_device(name=nm, mac=mm, novpn=False, nodns=True)
                    if ok:
                        flash_success("Device updated.")
                    else:
                        flash_error("Device update failed.")
                except ValueError as ve:
                    flash_error(str(ve))
                except Exception as e:
                    flash_error(f"Device update failed: {e}")
            elif "delete_device" in request.form:
                mac_orig = (request.form.get("edit_original_mac") or "").strip()
                try:
                    if not mac_orig:
                        raise ValueError("Original device MAC is missing.")
                    ok, _ = novpn_delete_device(mac_orig)
                    if ok:
                        flash_success("Device deleted.")
                    else:
                        flash_error("Device not found or delete failed.")
                except ValueError as ve:
                    flash_error(str(ve))
                except Exception as e:
                    flash_error(f"Device delete failed: {e}")
            else:
                # Handle save: update existing devices and add missing static DHCP devices
                existing = novpn_list_devices()
                existing_by_mac = {str(d.get("mac")).lower(): d for d in existing}
                # Merge with static leases not yet in config
                leases = read_static_leases()
                merged: list[dict] = []
                merged.extend(existing)
                for lease in leases:
                    mac_l = str(lease.get("mac", "")).lower()
                    if mac_l and mac_l not in existing_by_mac:
                        name_l = (
                            lease.get("hostname") or lease.get("ip") or lease.get("mac") or ""
                        ).strip()
                        merged.append({"name": name_l, "mac": mac_l, "novpn": False, "nodns": True})

                errors = []
                for d in merged:
                    mac = str(d.get("mac"))
                    novpn_val = bool(request.form.get(f"novpn_{mac}"))
                    nodns_val = bool(request.form.get(f"nodns_{mac}"))
                    if mac in existing_by_mac:
                        ok, _ = novpn_update_device_flags(mac, novpn=novpn_val, nodns=nodns_val)
                    else:
                        # Add new static entry with selected flags
                        try:
                            ok, _ = novpn_add_device(
                                name=str(d.get("name") or mac),
                                mac=mac,
                                novpn=novpn_val,
                                nodns=nodns_val,
                            )
                        except Exception:
                            ok = False
                    if not ok:
                        errors.append(mac)
                if errors:
                    flash_error("Saving failed for some devices: " + ", ".join(errors))
                else:
                    flash_success("Settings saved.")

        # Build devices for display: existing + static DHCP not yet in config (default nodns=True)
        devices = novpn_list_devices()
        existing_macs = {str(d.get("mac")).lower() for d in devices}
        static_leases_all = read_static_leases()
        extra_rows = []
        for lease in static_leases_all:
            mac_l = str(lease.get("mac", "")).lower()
            if not mac_l or mac_l in existing_macs:
                continue
            name_l = (lease.get("hostname") or lease.get("ip") or lease.get("mac") or "").strip()
            extra_rows.append(
                {"name": name_l, "mac": mac_l, "novpn": False, "nodns": True, "extra": True}
            )
        # extend devices with static rows for rendering
        devices = list(devices) + extra_rows
    except Exception as e:
        devices = []
        flash_error(f"Failed to process devices: {e}")
    return render_template("novpn.html", devices=devices)
