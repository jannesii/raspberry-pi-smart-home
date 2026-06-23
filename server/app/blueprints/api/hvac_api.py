"""HVAC and AC status related API routes.

Endpoints:
- /ac/status (GET)
- /hvac/avg_rates_today (GET)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

import pytz
from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required

if TYPE_CHECKING:
    from ...core import Controller
    from ...services.ac import ACThermostat

hvac_bp = Blueprint("api_hvac", __name__, url_prefix="")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@hvac_bp.route("/ac/status")
@login_required
def get_ac_status():
    """Return current AC on/off status from the running thermostat."""
    ac_thermo: ACThermostat = getattr(current_app, "ac_thermostat", None)  # type: ignore
    if ac_thermo is None:
        logger.warning("API /ac/status requested but thermostat not initialized")
        return jsonify(
            {
                "is_on": None,
                "thermostat_enabled": None,
                "thermo_active": None,
                "mode": None,
                "fan_speed": None,
                "sleep_enabled": None,
                "sleep_start": None,
                "sleep_stop": None,
                "sleep_time_active": None,
                "sleep_schedule": None,
                "sleep_for_active": None,
                "sleep_for_until": None,
            }
        ), 503
    try:
        enabled = getattr(ac_thermo, "_enabled", True)
        sleep_status = ac_thermo._sleep.get_status_payload()
        try:
            st = ac_thermo.ac.get_status()
            mode = st.get("mode") if isinstance(st, dict) else None
            fan = st.get("fan_speed_enum") if isinstance(st, dict) else None
        except Exception:
            mode = None
            fan = None
        payload = {
            "is_on": bool(ac_thermo.is_on),
            "thermostat_enabled": bool(enabled),
            "thermo_active": bool(enabled),
            "mode": mode,
            "fan_speed": fan,
            "setpoint_c": float(getattr(ac_thermo.cfg, "target_temp", 0.0)),
            "pos_hysteresis": float(getattr(ac_thermo.cfg, "pos_hysteresis", 0.0)),
            "neg_hysteresis": float(getattr(ac_thermo.cfg, "neg_hysteresis", 0.0)),
            "min_on_s": int(getattr(ac_thermo.cfg, "min_on_s", 240)),
            "min_off_s": int(getattr(ac_thermo.cfg, "min_off_s", 240)),
            "poll_interval_s": int(getattr(ac_thermo.cfg, "poll_interval_s", 15)),
            "smooth_window": int(getattr(ac_thermo.cfg, "smooth_window", 5)),
            "max_stale_s": getattr(ac_thermo.cfg, "max_stale_s", None),
            "control_locations": getattr(ac_thermo.cfg, "control_locations", None),
            **sleep_status,
        }
        logger.debug(
            (
                "API /ac/status override_active=%s override_until=%s "
                "sleep_for_active=%s sleep_for_until=%s"
            ),
            payload["sleep_override_active"],
            payload["sleep_override_until"],
            payload["sleep_for_active"],
            payload["sleep_for_until"],
        )
        return jsonify(payload)
    except Exception as e:
        logger.exception("Error reading AC status: %s", e)
        return jsonify(
            {
                "is_on": None,
                "thermostat_enabled": None,
                "thermo_active": None,
                "mode": None,
                "fan_speed": None,
                "sleep_enabled": None,
                "sleep_start": None,
                "sleep_stop": None,
                "sleep_for_active": None,
                "sleep_for_until": None,
            }
        ), 500


@hvac_bp.route("/hvac/avg_rates_today")
@login_required
def hvac_avg_rates_today():
    """
    Compute today's average cooling and heating rates from ESP32 readings + AC events.
    - Returns rates in °C/h, and if ROOM_THERMAL_CAPACITY_J_PER_K is set, also W.
    - Location defaults to THERMOSTAT_LOCATION.
    """
    ctrl: Controller = current_app.ctrl  # type: ignore
    finland_tz = pytz.timezone("Europe/Helsinki")
    # Prefer explicit query param, then app config, then env var, then 'default'
    location = request.args.get("location")
    if not location:
        try:
            location = current_app.config.get("THERMOSTAT_LOCATION")  # type: ignore
        except Exception:
            location = None
    if not location:
        location = os.getenv("THERMOSTAT_LOCATION", "Tietokonepöytä")

    now_local = datetime.now(finland_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    date_str = start_local.date().isoformat()

    readings = ctrl.get_esp32_temphum_for_date(date_str, location)
    logger.debug(
        "avg_rates: location=%s date=%s (Helsinki) readings=%d", location, date_str, len(readings)
    )

    # Prepare events with initial state
    start_iso = start_local.isoformat()
    end_iso = now_local.isoformat()
    events = ctrl.get_ac_events_between(start_iso, end_iso)
    init_state = ctrl.get_last_ac_state_before(start_iso)
    logger.debug(
        "avg_rates: window=%s..%s events=%d init_state=%s",
        start_iso,
        end_iso,
        len(events),
        init_state,
    )
    if init_state is None:
        try:
            conf = ctrl.get_thermostat_conf()
            if conf and conf.current_phase in ("on", "off"):
                init_state = conf.current_phase == "on"
        except Exception:
            init_state = None

    def _parse_iso(s: str):
        try:
            x = s.strip()
            if x.endswith("Z"):
                x = x[:-1]
            dt = datetime.fromisoformat(x)
            if dt.tzinfo is None:
                dt = finland_tz.localize(dt)
            return dt
        except Exception:
            return None

    ev_idx = 0
    cur_state = init_state
    ev_dts = []
    for e in events:
        dt = _parse_iso(e["timestamp"])
        if dt is not None:
            ev_dts.append((dt, bool(e["is_on"])))
    if ev_dts:
        logger.debug(
            "avg_rates: first_events=%s",
            [ev_dts[i][0].isoformat() for i in range(min(5, len(ev_dts)))],
        )
    else:
        logger.debug("avg_rates: no events inside window")

    points: list[tuple[datetime, float, bool | None]] = []
    skipped_ts = 0
    for r in readings:
        ts = _parse_iso(r.timestamp)
        if ts is None:
            skipped_ts += 1
            continue
        while ev_idx < len(ev_dts) and ev_dts[ev_idx][0] <= ts:
            cur_state = ev_dts[ev_idx][1]
            ev_idx += 1
        try:
            tval = float(r.temperature)
        except Exception:
            continue
        points.append((ts, tval, cur_state))
    if points:
        logger.debug(
            "avg_rates: points=%d first_ts=%s last_ts=%s skipped_ts=%d",
            len(points),
            points[0][0].isoformat(),
            points[-1][0].isoformat(),
            skipped_ts,
        )
    else:
        logger.debug("avg_rates: points=0 skipped_ts=%d", skipped_ts)

    if len(points) < 2:
        return jsonify(
            {
                "location": location,
                "date": date_str,
                "cooling_rate_c_per_h": None,
                "heating_rate_c_per_h": None,
                "cooling_power_w": None,
                "heating_power_w": None,
                "time_on_s": 0,
                "time_off_s": 0,
                "pairs_on": 0,
                "pairs_off": 0,
            }
        )

    points.sort(key=lambda x: x[0])
    MAX_GAP_S = 15 * 60
    sum_dt_on = 0.0
    sum_dT_on = 0.0
    sum_dt_off = 0.0
    sum_dT_off = 0.0
    pairs_on = 0
    pairs_off = 0
    skipped_no_state = 0
    skipped_bad_gap = 0
    skipped_nonpos_dt = 0

    for i in range(1, len(points)):
        t0, T0, s0 = points[i - 1]
        t1, T1, _s1 = points[i]
        if s0 is None:
            skipped_no_state += 1
            continue
        dt = (t1 - t0).total_seconds()
        if dt <= 0:
            skipped_nonpos_dt += 1
            continue
        if dt > MAX_GAP_S:
            skipped_bad_gap += 1
            continue
        dT = T1 - T0
        if s0 is True:
            sum_dt_on += dt
            sum_dT_on += dT
            pairs_on += 1
        else:
            sum_dt_off += dt
            sum_dT_off += dT
            pairs_off += 1

    cooling_rate_per_s = (sum_dT_on / sum_dt_on) if sum_dt_on > 0 else None
    heating_rate_per_s = (sum_dT_off / sum_dt_off) if sum_dt_off > 0 else None

    cooling_rate_c_per_h = (cooling_rate_per_s * 3600.0) if cooling_rate_per_s is not None else None
    heating_rate_c_per_h = (heating_rate_per_s * 3600.0) if heating_rate_per_s is not None else None
    logger.debug(
        "avg_rates: sums on(dt=%.1fs,dT=%.3f) off(dt=%.1fs,dT=%.3f) pairs on=%d off=%d skipped(no_state=%d, nonpos_dt=%d, big_gap=%d)",
        sum_dt_on,
        sum_dT_on,
        sum_dt_off,
        sum_dT_off,
        pairs_on,
        pairs_off,
        skipped_no_state,
        skipped_nonpos_dt,
        skipped_bad_gap,
    )

    cap_env = (
        current_app.config.get("ROOM_THERMAL_CAPACITY_J_PER_K")  # type: ignore
        or current_app.config.get("room_thermal_capacity_j_per_k")
        or os.getenv("ROOM_THERMAL_CAPACITY_J_PER_K")
    )
    try:
        Ceq = float(cap_env) if cap_env is not None else None
        if Ceq is not None and (Ceq <= 0 or not (Ceq < float("inf"))):
            Ceq = None
    except Exception:
        Ceq = None

    cooling_power_w = None
    heating_power_w = None
    if Ceq is not None:
        if cooling_rate_per_s is not None:
            cooling_power_w = max(0.0, -cooling_rate_per_s * Ceq)
        if heating_rate_per_s is not None:
            heating_power_w = max(0.0, heating_rate_per_s * Ceq)
    logger.debug(
        "avg_rates: rates C/h cool=%s heat=%s Ceq=%s => power W cool=%s heat=%s",
        cooling_rate_c_per_h,
        heating_rate_c_per_h,
        Ceq,
        cooling_power_w,
        heating_power_w,
    )

    return jsonify(
        {
            "location": location,
            "date": date_str,
            "cooling_rate_c_per_h": cooling_rate_c_per_h,
            "heating_rate_c_per_h": heating_rate_c_per_h,
            "cooling_power_w": cooling_power_w,
            "heating_power_w": heating_power_w,
            "time_on_s": int(sum_dt_on),
            "time_off_s": int(sum_dt_off),
            "pairs_on": pairs_on,
            "pairs_off": pairs_off,
        }
    )
