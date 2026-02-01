from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...services.car_heater import ChargeModeState, KeepAtTempSettings
from ..models import CarHeaterStatus
from ..schema import car_heater_charge_mode, car_heater_keep_at_temp, car_heater_status

logger = logging.getLogger(__name__)


class CarHeaterMixin:
    def record_car_heater_status(self, status: CarHeaterStatus) -> CarHeaterStatus:
        """
        Insert a new car heater status row and return it with id set.
        Uses the timestamp provided in the CarHeaterStatus.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "record_car_heater_status: ts=%s, on=%s", status.timestamp, status.is_heater_on
        )

        try:
            stmt = (
                insert(car_heater_status)
                .values(
                    timestamp=status.timestamp,
                    is_heater_on=status.is_heater_on,
                    instant_power_w=float(status.instant_power_w)
                    if status.instant_power_w is not None
                    else None,
                    voltage_v=status.voltage_v,
                    current_a=status.current_a,
                    energy_total_wh=status.energy_total_wh,
                    energy_last_min_wh=status.energy_last_min_wh,
                    energy_ts=status.energy_ts,
                    device_temp_c=status.device_temp_c,
                    device_temp_f=status.device_temp_f,
                    ambient_temp=status.ambient_temp,
                    source=status.source,
                )
                .returning(car_heater_status.c.id)
            )

            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
                new_id = result.scalar_one()

            return CarHeaterStatus(
                id=new_id,
                timestamp=status.timestamp,
                is_heater_on=status.is_heater_on,
                instant_power_w=status.instant_power_w,
                voltage_v=status.voltage_v,
                current_a=status.current_a,
                energy_total_wh=status.energy_total_wh,
                energy_last_min_wh=status.energy_last_min_wh,
                energy_ts=status.energy_ts,
                device_temp_c=status.device_temp_c,
                device_temp_f=status.device_temp_f,
                ambient_temp=status.ambient_temp,
                source=status.source,
            )
        except Exception as e:
            logger.exception("Error recording car heater status: %s", e)
            raise

    def get_last_car_heater_status(self) -> CarHeaterStatus | None:
        """
        Return the most recent car heater status row, or None if table is empty.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = (
                select(car_heater_status)
                .order_by(
                    car_heater_status.c.timestamp.desc(),
                    car_heater_status.c.id.desc(),
                )
                .limit(1)
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
        except Exception as e:
            logger.exception("Error getting last car heater status: %s", e)
            raise

    def get_car_heater_status_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> list[CarHeaterStatus]:
        """
        Get all car heater status rows between two ISO timestamps (inclusive),
        ordered by timestamp.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = (
                select(car_heater_status)
                .where(car_heater_status.c.timestamp >= start_iso)
                .where(car_heater_status.c.timestamp <= end_iso)
                .order_by(car_heater_status.c.timestamp, car_heater_status.c.id)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterStatus(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    is_heater_on=bool(row["is_heater_on"]),
                    instant_power_w=row["instant_power_w"],
                    voltage_v=row["voltage_v"],
                    current_a=row["current_a"],
                    energy_total_wh=row["energy_total_wh"],
                    energy_last_min_wh=row["energy_last_min_wh"],
                    energy_ts=row["energy_ts"],
                    device_temp_c=row["device_temp_c"],
                    device_temp_f=row["device_temp_f"],
                    ambient_temp=row["ambient_temp"],
                    source=row["source"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting car heater status between: %s", e)
            raise

    def get_recent_car_heater_status(
        self,
        limit: int = 200,
    ) -> list[CarHeaterStatus]:
        """
        Get the latest N car heater status rows, newest first.
        Handy for graphs.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = (
                select(car_heater_status)
                .order_by(
                    car_heater_status.c.timestamp.desc(),
                    car_heater_status.c.id.desc(),
                )
                .limit(limit)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterStatus(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    is_heater_on=bool(row["is_heater_on"]),
                    instant_power_w=row["instant_power_w"],
                    voltage_v=row["voltage_v"],
                    current_a=row["current_a"],
                    energy_total_wh=row["energy_total_wh"],
                    energy_last_min_wh=row["energy_last_min_wh"],
                    energy_ts=row["energy_ts"],
                    device_temp_c=row["device_temp_c"],
                    device_temp_f=row["device_temp_f"],
                    ambient_temp=row["ambient_temp"],
                    source=row["source"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting recent car heater status: %s", e)
            raise

    def get_car_heater_status_for_date(
        self,
        date_str: str,
    ) -> list[CarHeaterStatus]:
        """
        Return all car heater status records for a given local date.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            # Use func.date() for PostgreSQL compatibility
            stmt = (
                select(car_heater_status)
                .where(func.date(car_heater_status.c.timestamp) == date_str)
                .order_by(car_heater_status.c.timestamp, car_heater_status.c.id)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                CarHeaterStatus(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    is_heater_on=bool(row["is_heater_on"]),
                    instant_power_w=row["instant_power_w"],
                    voltage_v=row["voltage_v"],
                    current_a=row["current_a"],
                    energy_total_wh=row["energy_total_wh"],
                    energy_last_min_wh=row["energy_last_min_wh"],
                    energy_ts=row["energy_ts"],
                    device_temp_c=row["device_temp_c"],
                    device_temp_f=row["device_temp_f"],
                    ambient_temp=row["ambient_temp"],
                    source=row["source"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error getting car heater status for date: %s", e)
            raise

    # --- Car heater charge mode state ---
    def get_charge_mode_state(self) -> ChargeModeState:
        """Load ChargeModeState from the database, or return defaults if not found."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(car_heater_charge_mode).where(car_heater_charge_mode.c.id == 1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return ChargeModeState()

            return ChargeModeState(
                enabled=bool(row["enabled"]),
                threshold_w=float(row["threshold_w"]),
                power_cut=bool(row["power_cut"]),
                power_cut_at=row["power_cut_at"],
                last_instant_power_w=float(row["last_instant_power_w"])
                if row["last_instant_power_w"] is not None
                else None,
                seen_above_threshold=bool(row["seen_above_threshold"]),
            )
        except Exception as e:
            logger.exception("Error getting charge mode state: %s", e)
            raise

    def save_charge_mode_state(self, state: ChargeModeState) -> None:
        """Persist ChargeModeState to the database."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = pg_insert(car_heater_charge_mode).values(
                id=1,
                enabled=state.enabled,
                threshold_w=state.threshold_w,
                power_cut=state.power_cut,
                power_cut_at=state.power_cut_at,
                last_instant_power_w=state.last_instant_power_w,
                seen_above_threshold=state.seen_above_threshold,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "enabled": stmt.excluded.enabled,
                    "threshold_w": stmt.excluded.threshold_w,
                    "power_cut": stmt.excluded.power_cut,
                    "power_cut_at": stmt.excluded.power_cut_at,
                    "last_instant_power_w": stmt.excluded.last_instant_power_w,
                    "seen_above_threshold": stmt.excluded.seen_above_threshold,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving charge mode state: %s", e)
            raise

    # --- Car heater keep-at-temp settings ---
    def get_keep_at_temp_settings(self) -> KeepAtTempSettings:
        """Load KeepAtTempSettings from the database, or return defaults if not found."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(car_heater_keep_at_temp).where(car_heater_keep_at_temp.c.id == 1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return KeepAtTempSettings()

            return KeepAtTempSettings(
                target_temperature_c=float(row["target_temperature_c"])
                if row["target_temperature_c"] is not None
                else None,
                hysteresis_c=float(row["hysteresis_c"])
                if row["hysteresis_c"] is not None
                else None,
                enabled=bool(row["enabled"]) if row["enabled"] is not None else None,
            )
        except Exception as e:
            logger.exception("Error getting keep at temp settings: %s", e)
            raise

    def save_keep_at_temp_settings(self, settings: KeepAtTempSettings) -> None:
        """Persist KeepAtTempSettings to the database."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = pg_insert(car_heater_keep_at_temp).values(
                id=1,
                target_temperature_c=settings.target_temperature_c,
                hysteresis_c=settings.hysteresis_c,
                enabled=settings.enabled,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "target_temperature_c": stmt.excluded.target_temperature_c,
                    "hysteresis_c": stmt.excluded.hysteresis_c,
                    "enabled": stmt.excluded.enabled,
                },
            )

            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving keep at temp settings: %s", e)
            raise

    # --- Migration helper ---
    def migrate_car_heater_to_pg(self, batch_size: int = 1000) -> dict[str, Any]:
        """
        Migrate car heater data from SQLite to PostgreSQL using bulk inserts.
        Returns dict with migration statistics.

        Args:
            batch_size: Number of rows to insert per batch (default 1000)
        """
        import time

        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        stats = {
            "car_heater_status": {"migrated": 0, "errors": 0},
            "car_heater_charge_mode": {"migrated": 0, "errors": 0},
            "car_heater_keep_at_temp": {"migrated": 0, "errors": 0},
        }

        logger.info("=" * 60)
        logger.info("Starting car heater data migration from SQLite to PostgreSQL")
        logger.info("Batch size: %d rows per transaction", batch_size)
        logger.info("=" * 60)

        # Migrate car_heater_status in batches
        try:
            rows = self.db.fetchall(
                """
                SELECT id, timestamp, is_heater_on, instant_power_w, voltage_v,
                       current_a, energy_total_wh, energy_last_min_wh, energy_ts,
                       device_temp_c, device_temp_f, ambient_temp, source
                FROM car_heater_status ORDER BY id
                """
            )
            total = len(rows)
            logger.info("📊 car_heater_status: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ car_heater_status: No records to migrate")
            else:
                start_time = time.time()
                batch = []
                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"],
                            "is_heater_on": row["is_heater_on"],
                            "instant_power_w": row["instant_power_w"],
                            "voltage_v": row["voltage_v"],
                            "current_a": row["current_a"],
                            "energy_total_wh": row["energy_total_wh"],
                            "energy_last_min_wh": row["energy_last_min_wh"],
                            "energy_ts": row["energy_ts"],
                            "device_temp_c": row["device_temp_c"],
                            "device_temp_f": row["device_temp_f"],
                            "ambient_temp": row["ambient_temp"],
                            "source": row["source"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            stmt = pg_insert(car_heater_status).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            with sa_engine.begin() as conn:
                                conn.execute(stmt)
                            stats["car_heater_status"]["migrated"] += len(batch)

                            elapsed = time.time() - start_time
                            progress_pct = (stats["car_heater_status"]["migrated"] / total) * 100
                            rate = (
                                stats["car_heater_status"]["migrated"] / elapsed
                                if elapsed > 0
                                else 0
                            )
                            remaining = (
                                (total - stats["car_heater_status"]["migrated"]) / rate
                                if rate > 0
                                else 0
                            )

                            logger.info(
                                "📈 car_heater_status: %d/%d (%.1f%%) | %.0f rows/sec | ETA: %.0fs",
                                stats["car_heater_status"]["migrated"],
                                total,
                                progress_pct,
                                rate,
                                remaining,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating car_heater_status batch: %s", e)
                            stats["car_heater_status"]["errors"] += len(batch)
                            batch = []

                elapsed = time.time() - start_time
                logger.info(
                    "✓ car_heater_status: Completed in %.1fs (%.0f rows/sec)",
                    elapsed,
                    total / elapsed if elapsed > 0 else 0,
                )

        except Exception as e:
            logger.exception("Error reading car_heater_status from SQLite: %s", e)

        # Migrate car_heater_charge_mode (singleton upsert)
        try:
            rows = self.db.fetchall(
                """
                SELECT id, enabled, threshold_w, power_cut, power_cut_at,
                       last_instant_power_w, seen_above_threshold
                FROM car_heater_charge_mode
                """
            )
            total = len(rows)
            logger.info("📊 car_heater_charge_mode: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ car_heater_charge_mode: No records to migrate")
            else:
                for row in rows:
                    try:
                        stmt = pg_insert(car_heater_charge_mode).values(
                            id=row["id"],
                            enabled=row["enabled"],
                            threshold_w=row["threshold_w"],
                            power_cut=row["power_cut"],
                            power_cut_at=row["power_cut_at"],
                            last_instant_power_w=row["last_instant_power_w"],
                            seen_above_threshold=row["seen_above_threshold"],
                        )
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])

                        with sa_engine.begin() as conn:
                            conn.execute(stmt)

                        stats["car_heater_charge_mode"]["migrated"] += 1
                    except Exception as e:
                        logger.error("❌ Error migrating car_heater_charge_mode: %s", e)
                        stats["car_heater_charge_mode"]["errors"] += 1

                logger.info(
                    "✓ car_heater_charge_mode: %d migrated, %d errors",
                    stats["car_heater_charge_mode"]["migrated"],
                    stats["car_heater_charge_mode"]["errors"],
                )

        except Exception as e:
            logger.exception("Error reading car_heater_charge_mode from SQLite: %s", e)

        # Migrate car_heater_keep_at_temp (singleton upsert)
        try:
            rows = self.db.fetchall(
                """
                SELECT id, target_temperature_c, hysteresis_c, enabled
                FROM car_heater_keep_at_temp
                """
            )
            total = len(rows)
            logger.info("📊 car_heater_keep_at_temp: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ car_heater_keep_at_temp: No records to migrate")
            else:
                for row in rows:
                    try:
                        stmt = pg_insert(car_heater_keep_at_temp).values(
                            id=row["id"],
                            target_temperature_c=row["target_temperature_c"],
                            hysteresis_c=row["hysteresis_c"],
                            enabled=row["enabled"],
                        )
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])

                        with sa_engine.begin() as conn:
                            conn.execute(stmt)

                        stats["car_heater_keep_at_temp"]["migrated"] += 1
                    except Exception as e:
                        logger.error("❌ Error migrating car_heater_keep_at_temp: %s", e)
                        stats["car_heater_keep_at_temp"]["errors"] += 1

                logger.info(
                    "✓ car_heater_keep_at_temp: %d migrated, %d errors",
                    stats["car_heater_keep_at_temp"]["migrated"],
                    stats["car_heater_keep_at_temp"]["errors"],
                )

        except Exception as e:
            logger.exception("Error reading car_heater_keep_at_temp from SQLite: %s", e)

        logger.info("=" * 60)
        logger.info("Car heater migration summary:")
        for table, counts in stats.items():
            logger.info("  %s: %d migrated, %d errors", table, counts["migrated"], counts["errors"])
        logger.info("=" * 60)

        return stats
