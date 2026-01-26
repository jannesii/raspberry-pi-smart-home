import logging
from datetime import datetime

from ..models import ThermostatConf

logger = logging.getLogger(__name__)


class ACMixin:
    # --- AC event logging / queries ---
    def record_ac_event(
        self,
        is_on: bool,
        source: str | None = None,
        note: str | None = None,
        when_iso: str | None = None,
    ) -> None:
        """Insert an AC on/off event.

        :param is_on: True for ON, False for OFF
        :param source: optional tag (e.g., 'thermostat', 'manual')
        :param note: optional message
        :param when_iso: ISO timestamp; if None, uses local now
        """
        ts = when_iso or datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO ac_events (timestamp, is_on, source, note) VALUES (?, ?, ?, ?)",
            (ts, 1 if is_on else 0, source, note),
        )

    def get_ac_events_between(self, start_iso: str, end_iso: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT id, timestamp, is_on, source, note FROM ac_events WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_iso, end_iso),
        )
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "is_on": bool(row["is_on"]),
                "source": row["source"],
                "note": row["note"],
            }
            for row in rows
        ]

    def get_last_ac_state_before(self, ts_iso: str) -> bool | None:
        row = self.db.fetchone(
            "SELECT is_on FROM ac_events WHERE timestamp <= ? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (ts_iso,),
        )
        if row is None:
            return None
        return bool(row["is_on"])

    # --- Thermostat configuration operations ---
    def get_thermostat_conf(self) -> ThermostatConf | None:
        logger.debug("get_thermostat_conf called")
        row = self.db.fetchone(
            """
            SELECT id, sleep_active, sleep_start, sleep_stop, sleep_weekly, control_locations, target_temp, pos_hysteresis, neg_hysteresis, thermo_active,
                   total_on_s, total_off_s,
                   min_on_s, min_off_s, poll_interval_s, smooth_window, max_stale_s,
                   current_phase, phase_started_at
              FROM thermostat_conf
             WHERE id = 1
            """
        )
        if row is None:
            logger.debug("get_thermostat_conf no row found")
            return None
        logger.debug("get_thermostat_conf row keys: %s", list(row.keys()))
        return ThermostatConf(
            id=row["id"],
            sleep_active=bool(row["sleep_active"]),
            sleep_start=row["sleep_start"],
            sleep_stop=row["sleep_stop"],
            sleep_weekly=(row["sleep_weekly"]),
            control_locations=(row["control_locations"]),
            target_temp=float(row["target_temp"]),
            pos_hysteresis=float(row["pos_hysteresis"]),
            neg_hysteresis=float(row["neg_hysteresis"]),
            thermo_active=bool(row["thermo_active"]) if "thermo_active" in row else True,
            min_on_s=int(row["min_on_s"]) if "min_on_s" in row else 240,
            min_off_s=int(row["min_off_s"]) if "min_off_s" in row else 240,
            poll_interval_s=int(row["poll_interval_s"]) if "poll_interval_s" in row else 15,
            smooth_window=int(row["smooth_window"]) if "smooth_window" in row else 5,
            max_stale_s=int(row["max_stale_s"])
            if "max_stale_s" in row and row["max_stale_s"] is not None
            else 120,
            current_phase=row["current_phase"],
            phase_started_at=row["phase_started_at"],
        )

    def save_thermostat_conf(
        self,
        *,
        sleep_active: bool,
        sleep_start: str | None,
        sleep_stop: str | None,
        sleep_weekly: str | None = None,
        control_locations: str | None = None,
        target_temp: float,
        pos_hysteresis: float,
        neg_hysteresis: float,
        thermo_active: bool,
        # historical totals no longer used; kept for backward compat at DB level
        total_on_s: int = 0,
        total_off_s: int = 0,
        min_on_s: int = 240,
        min_off_s: int = 240,
        poll_interval_s: int = 15,
        smooth_window: int = 5,
        max_stale_s: int | None = 120,
        current_phase: str | None = None,
        phase_started_at: str | None = None,
    ) -> ThermostatConf:
        self.db.execute_query(
            """
            INSERT INTO thermostat_conf (id, sleep_active, sleep_start, sleep_stop, sleep_weekly, control_locations, target_temp, pos_hysteresis, neg_hysteresis, thermo_active,
                                         total_on_s, total_off_s, min_on_s, min_off_s, poll_interval_s, smooth_window, max_stale_s,
                                         current_phase, phase_started_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sleep_active = excluded.sleep_active,
                sleep_start = excluded.sleep_start,
                sleep_stop = excluded.sleep_stop,
                sleep_weekly = excluded.sleep_weekly,
                control_locations = excluded.control_locations,
                target_temp = excluded.target_temp,
                pos_hysteresis = excluded.pos_hysteresis,
                neg_hysteresis = excluded.neg_hysteresis,
                thermo_active = excluded.thermo_active,
                total_on_s = excluded.total_on_s,
                total_off_s = excluded.total_off_s,
                min_on_s = excluded.min_on_s,
                min_off_s = excluded.min_off_s,
                poll_interval_s = excluded.poll_interval_s,
                smooth_window = excluded.smooth_window,
                max_stale_s = excluded.max_stale_s,
                current_phase = excluded.current_phase,
                phase_started_at = excluded.phase_started_at
            """,
            (
                1 if sleep_active else 0,
                sleep_start,
                sleep_stop,
                sleep_weekly,
                control_locations,
                float(target_temp),
                float(pos_hysteresis),
                float(neg_hysteresis),
                1 if thermo_active else 0,
                int(total_on_s),
                int(total_off_s),
                int(min_on_s),
                int(min_off_s),
                int(poll_interval_s),
                int(smooth_window),
                None if max_stale_s is None else int(max_stale_s),
                current_phase,
                phase_started_at,
            ),
        )
        conf = self.get_thermostat_conf()
        if conf is None:
            # This should never happen after UPSERT
            raise RuntimeError("Failed to save thermostat configuration")
        return conf

    def ensure_thermostat_conf_seeded_from(self, cfg: object | None = None) -> ThermostatConf:
        """
        Seed the thermostat configuration row from a given config-like object
        that provides attributes: setpoint_c, pos_hysteresis, neg_hysteresis, sleep_enabled,
        sleep_start, sleep_stop. If a row already exists, it is returned as-is.
        """
        existing = self.get_thermostat_conf()
        if existing is not None:
            return existing
        # Extract with safe fallbacks (support legacy names too)

        def _getattr(name: str, default):
            if cfg is None:
                return default
            return getattr(cfg, name, default)

        target_temp = float(_getattr("target_temp", _getattr("setpoint_c", 24.5)))
        pos_h = float(_getattr("pos_hysteresis", 0.5))
        neg_h = float(_getattr("neg_hysteresis", 0.5))
        sleep_active = bool(_getattr("sleep_active", _getattr("sleep_enabled", True)))
        thermo_active = bool(_getattr("thermo_active", True))
        sleep_start = _getattr("sleep_start", None)
        sleep_stop = _getattr("sleep_stop", None)
        total_on_s = int(_getattr("total_on_s", 0) or 0)
        total_off_s = int(_getattr("total_off_s", 0) or 0)
        min_on_s = int(_getattr("min_on_s", 240))
        min_off_s = int(_getattr("min_off_s", 240))
        poll_interval_s = int(_getattr("poll_interval_s", 15))
        smooth_window = int(_getattr("smooth_window", 5))
        max_stale_s = _getattr("max_stale_s", 120)
        try:
            max_stale_s = None if max_stale_s is None else int(max_stale_s)
        except Exception:
            max_stale_s = 120
        return self.save_thermostat_conf(
            sleep_active=sleep_active,
            sleep_start=sleep_start,
            sleep_stop=sleep_stop,
            total_on_s=total_on_s,
            total_off_s=total_off_s,
            target_temp=target_temp,
            pos_hysteresis=pos_h,
            neg_hysteresis=neg_h,
            thermo_active=thermo_active,
            min_on_s=min_on_s,
            min_off_s=min_off_s,
            poll_interval_s=poll_interval_s,
            smooth_window=smooth_window,
            max_stale_s=max_stale_s,
            current_phase=_getattr("current_phase", "off"),
            phase_started_at=_getattr("phase_started_at", None),
        )
