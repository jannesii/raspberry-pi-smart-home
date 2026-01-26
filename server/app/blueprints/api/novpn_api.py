"""NoVPN device management API routes.

Endpoints (all under /novpn):
- /devices (GET)
- /update (POST)
- /temp_bypass (POST)
- /quick_bypass (GET)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from ...extensions import csrf
from ...services.novpn import (
    add_device as novpn_add_device,
    delete_device as novpn_delete_device,
    list_devices as novpn_list_devices,
    update_device_flags as novpn_update_device_flags,
)
from ...utils import require_root_admin_or_redirect

novpn_bp = Blueprint("api_novpn", __name__, url_prefix="/novpn")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@novpn_bp.route("/devices", methods=["GET"])
@login_required
def novpn_devices():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard
    try:
        devices = novpn_list_devices()
        return jsonify({"ok": True, "devices": devices})
    except Exception as e:
        logger.exception("Failed to read novpn devices: %s", e)
        return jsonify({"ok": False, "error": "read_failed", "message": str(e)}), 500


@novpn_bp.route("/update", methods=["POST"])
@csrf.exempt
@login_required
def novpn_update():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip()
    if not mac:
        return jsonify({"ok": False, "error": "invalid_payload", "message": "mac required"}), 400
    novpn = data.get("novpn")
    nodns = data.get("nodns")
    name = (data.get("name") or "").strip()
    if novpn is not None and not isinstance(novpn, bool):
        return jsonify(
            {"ok": False, "error": "invalid_payload", "message": "novpn must be boolean"}
        ), 400
    if nodns is not None and not isinstance(nodns, bool):
        return jsonify(
            {"ok": False, "error": "invalid_payload", "message": "nodns must be boolean"}
        ), 400
    try:
        ok, updated = novpn_update_device_flags(mac, novpn=novpn, nodns=nodns)
        if not ok or not updated:
            # If device not found, allow creating it when a name is provided
            if name:
                ok_add, dev = novpn_add_device(
                    name=name,
                    mac=mac,
                    novpn=bool(novpn) if novpn is not None else False,
                    nodns=bool(nodns) if nodns is not None else False,
                )
                if not ok_add:
                    return jsonify(
                        {"ok": False, "error": "write_failed", "message": "Failed to add device"}
                    ), 500
                return jsonify({"ok": True, "device": dev})
            return jsonify({"ok": False, "error": "not_found", "message": "Device not found"}), 404
        return jsonify({"ok": True, "device": updated})
    except Exception as e:
        logger.exception("Failed to update novpn device: %s", e)
        return jsonify({"ok": False, "error": "write_failed", "message": str(e)}), 500


@novpn_bp.route("/temp_bypass", methods=["POST"])
@csrf.exempt
@login_required
def novpn_temp_bypass():
    guard = require_root_admin_or_redirect("Root-Admin required", json=True)
    if guard:
        return guard
    try:
        import eventlet
    except Exception:
        eventlet = None  # type: ignore

    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip()
    if not mac:
        return jsonify({"ok": False, "error": "invalid_payload", "message": "mac required"}), 400
    minutes = int(data.get("minutes") or 15)
    minutes = max(1, min(minutes, 240))
    name = (data.get("name") or "").strip() or mac

    try:
        # Snapshot existing state (if any)
        existing = {d.get("mac"): d for d in novpn_list_devices()}
        existed = mac in existing
        original_novpn = bool(existing.get(mac, {}).get("novpn")) if existed else False

        if existed:
            ok, _ = novpn_update_device_flags(mac, novpn=True, nodns=None)
            if not ok:
                return jsonify(
                    {"ok": False, "error": "write_failed", "message": "failed to set novpn"}
                ), 500
        else:
            # Create ephemeral entry with nodns=False per requirement
            ok, _ = novpn_add_device(name=name, mac=mac, novpn=True, nodns=False)
            if not ok:
                return jsonify(
                    {
                        "ok": False,
                        "error": "write_failed",
                        "message": "failed to create temp device",
                    }
                ), 500

        # Log scheduling details
        now_utc = datetime.now(UTC)
        end_utc = now_utc + timedelta(minutes=minutes)
        logger.debug(
            "novpn_temp_bypass: mac=%s name=%s minutes=%d end=%s",
            mac,
            name,
            minutes,
            end_utc.isoformat(),
        )

        def _revert():  # executed later
            try:
                # If device did not exist originally, delete it; else restore original novpn
                if not existed:
                    novpn_delete_device(mac)
                else:
                    # Only revert if still in forced state (avoid clobbering user changes)
                    cur = {d.get("mac"): d for d in novpn_list_devices()}
                    cur_novpn = bool(cur.get(mac, {}).get("novpn")) if mac in cur else None
                    if cur_novpn is True or cur_novpn is None:
                        novpn_update_device_flags(mac, novpn=original_novpn, nodns=None)
            except Exception:
                logger.exception("temp_bypass revert failed for %s", mac)

        # Schedule revert
        try:
            if eventlet:
                eventlet.spawn_after(minutes * 60, _revert)
            else:
                import threading

                threading.Timer(minutes * 60, _revert).start()
        except Exception:
            logger.exception("Failed to schedule temp_bypass revert; reverting now")
            _revert()

        return jsonify({"ok": True, "mac": mac, "minutes": minutes})
    except Exception as e:
        logger.exception("Failed to temp-bypass novpn device: %s", e)
        return jsonify({"ok": False, "error": "write_failed", "message": str(e)}), 500


@novpn_bp.route("/quick_bypass", methods=["GET"])
@login_required
def novpn_quick_bypass():
    """Quickly enable VPN bypass for a fixed set of devices for 15 minutes.

    Requires Root-Admin. Returns a simple HTML template with results.
    """
    guard = require_root_admin_or_redirect("Root-Admin required")
    if guard:
        return guard

    try:
        import eventlet
    except Exception:
        eventlet = None  # type: ignore

    devices = [
        {"mac": "06:b5:60:53:4c:a9", "name": "iPhone"},
        {"mac": "4c:ed:fb:67:7f:9d", "name": "PC"},
    ]
    minutes = 15
    results: list[dict] = []

    existing_map = {d.get("mac"): d for d in novpn_list_devices()}

    def schedule_revert(mac: str, existed: bool, original_novpn: bool) -> None:
        def _revert():
            try:
                if not existed:
                    novpn_delete_device(mac)
                else:
                    cur = {d.get("mac"): d for d in novpn_list_devices()}
                    cur_novpn = bool(cur.get(mac, {}).get("novpn")) if mac in cur else None
                    if cur_novpn is True or cur_novpn is None:
                        novpn_update_device_flags(mac, novpn=original_novpn, nodns=None)
            except Exception:
                logger.exception("quick_bypass revert failed for %s", mac)

        try:
            if eventlet:
                eventlet.spawn_after(minutes * 60, _revert)
            else:
                import threading

                threading.Timer(minutes * 60, _revert).start()
        except Exception:
            logger.exception("Failed to schedule quick_bypass revert for %s; reverting now", mac)
            _revert()

    for item in devices:
        mac = item["mac"].strip().lower()
        name = (item.get("name") or mac).strip()
        try:
            existed = mac in existing_map
            original_novpn = bool(existing_map.get(mac, {}).get("novpn")) if existed else False
            if existed:
                ok, _ = novpn_update_device_flags(mac, novpn=True, nodns=None)
            else:
                ok, _ = novpn_add_device(name=name, mac=mac, novpn=True, nodns=False)
            if ok:
                now_utc = datetime.now(UTC)
                end_utc = now_utc + timedelta(minutes=minutes)
                logger.debug(
                    "novpn_quick_bypass: mac=%s name=%s minutes=%d end=%s",
                    mac,
                    name,
                    minutes,
                    end_utc.isoformat(),
                )
                schedule_revert(mac, existed, original_novpn)
                results.append({"mac": mac, "name": name, "ok": True, "minutes": minutes})
            else:
                results.append({"mac": mac, "name": name, "ok": False, "error": "write_failed"})
        except Exception as e:
            logger.exception("quick_bypass failed for %s", mac)
            results.append({"mac": mac, "name": name, "ok": False, "error": str(e)})

    overall_ok = all(r.get("ok") for r in results) and len(results) == len(devices)
    return render_template(
        "novpn_quick.html", results=results, minutes=minutes, overall_ok=overall_ok
    )
