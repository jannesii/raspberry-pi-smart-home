from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import ThermostatConf
from ..schema import ac_events, thermostat_conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        ts = when_iso or datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]
        logger.debug("record_ac_event: is_on=%s, source=%s", is_on, source)

        try:
            stmt = insert(ac_events).values(timestamp=ts, is_on=is_on, source=source, note=note)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error recording AC event: %s", e)
            raise

    def get_ac_events_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Get AC events between two timestamps."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_ac_events_between: %s to %s", start_iso, end_iso)

        try:
            stmt = (
                select(ac_events)
                .where(ac_events.c.timestamp >= start_iso)
                .where(ac_events.c.timestamp <= end_iso)
                .order_by(ac_events.c.timestamp)
            )
            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

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
        except Exception as e:
            logger.exception("Error getting AC events: %s", e)
            raise

    def get_last_ac_state_before(self, ts_iso: str) -> bool | None:
        """Get the last AC state (on/off) before a given timestamp."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_last_ac_state_before: %s", ts_iso)

        try:
            stmt = (
                select(ac_events.c.is_on)
                .where(ac_events.c.timestamp <= ts_iso)
                .order_by(ac_events.c.timestamp.desc(), ac_events.c.id.desc())
                .limit(1)
            )
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None
            return bool(row["is_on"])
        except Exception as e:
            logger.exception("Error getting last AC state: %s", e)
            raise

    # --- Thermostat configuration operations ---
    def get_thermostat_conf(self) -> ThermostatConf | None:
        """Get thermostat configuration (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_thermostat_conf called")

        try:
            stmt = select(thermostat_conf).where(thermostat_conf.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("get_thermostat_conf no row found")
                return None

            logger.debug("get_thermostat_conf row keys: %s", list(row.keys()))
            return ThermostatConf(
                id=row["id"],
                sleep_active=bool(row["sleep_active"]),
                sleep_start=row["sleep_start"],
                sleep_stop=row["sleep_stop"],
                sleep_weekly=row["sleep_weekly"],
                control_locations=row["control_locations"],
                target_temp=float(row["target_temp"]),
                pos_hysteresis=float(row["pos_hysteresis"]),
                neg_hysteresis=float(row["neg_hysteresis"]),
                thermo_active=bool(row.get("thermo_active", True)),
                min_on_s=int(row.get("min_on_s", 240)),
                min_off_s=int(row.get("min_off_s", 240)),
                poll_interval_s=int(row.get("poll_interval_s", 15)),
                smooth_window=int(row.get("smooth_window", 5)),
                max_stale_s=int(row["max_stale_s"]) if row.get("max_stale_s") is not None else 120,
                current_phase=row.get("current_phase"),
                phase_started_at=row.get("phase_started_at"),
            )
        except Exception as e:
            logger.exception("Error getting thermostat config: %s", e)
            raise

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
        """Save or update thermostat configuration (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = pg_insert(thermostat_conf).values(
                id=1,
                sleep_active=sleep_active,
                sleep_start=sleep_start,
                sleep_stop=sleep_stop,
                sleep_weekly=sleep_weekly,
                control_locations=control_locations,
                target_temp=float(target_temp),
                pos_hysteresis=float(pos_hysteresis),
                neg_hysteresis=float(neg_hysteresis),
                thermo_active=thermo_active,
                total_on_s=int(total_on_s),
                total_off_s=int(total_off_s),
                min_on_s=int(min_on_s),
                min_off_s=int(min_off_s),
                poll_interval_s=int(poll_interval_s),
                smooth_window=int(smooth_window),
                max_stale_s=None if max_stale_s is None else int(max_stale_s),
                current_phase=current_phase,
                phase_started_at=phase_started_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "sleep_active": stmt.excluded.sleep_active,
                    "sleep_start": stmt.excluded.sleep_start,
                    "sleep_stop": stmt.excluded.sleep_stop,
                    "sleep_weekly": stmt.excluded.sleep_weekly,
                    "control_locations": stmt.excluded.control_locations,
                    "target_temp": stmt.excluded.target_temp,
                    "pos_hysteresis": stmt.excluded.pos_hysteresis,
                    "neg_hysteresis": stmt.excluded.neg_hysteresis,
                    "thermo_active": stmt.excluded.thermo_active,
                    "total_on_s": stmt.excluded.total_on_s,
                    "total_off_s": stmt.excluded.total_off_s,
                    "min_on_s": stmt.excluded.min_on_s,
                    "min_off_s": stmt.excluded.min_off_s,
                    "poll_interval_s": stmt.excluded.poll_interval_s,
                    "smooth_window": stmt.excluded.smooth_window,
                    "max_stale_s": stmt.excluded.max_stale_s,
                    "current_phase": stmt.excluded.current_phase,
                    "phase_started_at": stmt.excluded.phase_started_at,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)

            conf = self.get_thermostat_conf()
            if conf is None:
                # This should never happen after UPSERT
                raise RuntimeError("Failed to save thermostat configuration")
            return conf
        except Exception as e:
            logger.exception("Error saving thermostat config: %s", e)
            raise

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

    # --- Migration helper ---
    def migrate_ac_to_pg(self, batch_size: int = 1000) -> dict[str, Any]:
        """
        Migrate AC data from SQLite to PostgreSQL using bulk inserts.
        Returns dict with migration statistics.

        Args:
            batch_size: Number of rows to insert per batch (default 1000)
        """
        import time

        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        stats = {
            "ac_events": {"migrated": 0, "errors": 0},
            "thermostat_conf": {"migrated": 0, "errors": 0},
        }

        logger.info("=" * 60)
        logger.info("Starting AC data migration from SQLite to PostgreSQL")
        logger.info("Batch size: %d rows per transaction", batch_size)
        logger.info("=" * 60)

        # Migrate ac_events in batches
        try:
            rows = self.db.fetchall(
                "SELECT id, timestamp, is_on, source, note FROM ac_events ORDER BY id"
            )
            total = len(rows)
            logger.info("📊 ac_events: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ ac_events: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"],
                            "is_on": row["is_on"],
                            "source": row["source"],
                            "note": row["note"],
                        }
                    )

                    # Insert when batch is full or at the end
                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(ac_events).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["ac_events"]["migrated"] += len(batch)

                            # Calculate progress
                            elapsed = time.time() - start_time
                            progress_pct = (stats["ac_events"]["migrated"] / total) * 100
                            rate = stats["ac_events"]["migrated"] / elapsed if elapsed > 0 else 0
                            remaining = (
                                (total - stats["ac_events"]["migrated"]) / rate if rate > 0 else 0
                            )

                            logger.info(
                                "📈 ac_events: %d/%d (%.1f%%) | %.0f rows/sec | ETA: %.0fs",
                                stats["ac_events"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                                remaining,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating ac_events batch: %s", e)
                            stats["ac_events"]["errors"] += len(batch)
                            batch = []

                elapsed = time.time() - start_time
                logger.info(
                    "✓ ac_events: Completed in %.1fs (%.0f rows/sec)",
                    elapsed,
                    total / elapsed if elapsed > 0 else 0,
                )

        except Exception as e:
            logger.exception("Error reading ac_events from SQLite: %s", e)

        # Migrate thermostat_conf (singleton upsert)
        try:
            rows = self.db.fetchall(
                """
                SELECT id, sleep_active, sleep_start, sleep_stop, sleep_weekly,
                       control_locations, target_temp, pos_hysteresis, neg_hysteresis,
                       thermo_active, total_on_s, total_off_s, min_on_s, min_off_s,
                       poll_interval_s, smooth_window, max_stale_s,
                       current_phase, phase_started_at
                FROM thermostat_conf
                """
            )
            total = len(rows)
            logger.info("📊 thermostat_conf: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ thermostat_conf: No records to migrate")
            else:
                for row in rows:
                    try:
                        stmt = pg_insert(thermostat_conf).values(
                            id=row["id"],
                            sleep_active=row["sleep_active"],
                            sleep_start=row["sleep_start"],
                            sleep_stop=row["sleep_stop"],
                            sleep_weekly=row["sleep_weekly"],
                            control_locations=row["control_locations"],
                            target_temp=row["target_temp"],
                            pos_hysteresis=row["pos_hysteresis"],
                            neg_hysteresis=row["neg_hysteresis"],
                            thermo_active=row["thermo_active"],
                            total_on_s=row["total_on_s"],
                            total_off_s=row["total_off_s"],
                            min_on_s=row["min_on_s"],
                            min_off_s=row["min_off_s"],
                            poll_interval_s=row["poll_interval_s"],
                            smooth_window=row["smooth_window"],
                            max_stale_s=row["max_stale_s"],
                            current_phase=row["current_phase"],
                            phase_started_at=row["phase_started_at"],
                        )
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])

                        with sa_engine.begin() as conn:
                            conn.execute(stmt)

                        stats["thermostat_conf"]["migrated"] += 1
                        logger.info("✓ thermostat_conf: %d migrated, 0 errors", total)
                    except Exception as e:
                        logger.error("❌ Error migrating thermostat_conf row: %s", e)
                        stats["thermostat_conf"]["errors"] += 1

        except Exception as e:
            logger.exception("Error reading thermostat_conf from SQLite: %s", e)

        logger.info("=" * 60)
        logger.info("AC migration summary:")
        for table, counts in stats.items():
            logger.info("  %s: %d migrated, %d errors", table, counts["migrated"], counts["errors"])
        logger.info("=" * 60)

        return stats
