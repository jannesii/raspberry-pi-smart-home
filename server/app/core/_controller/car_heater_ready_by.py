from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import CarHeaterReadyByConfig, CarHeaterReadyByState
from ..schema import car_heater_ready_by_config, car_heater_ready_by_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CarHeaterReadyByMixin:
    # --- Ready-by state (single-row settings) ---
    def get_ready_by_state(self) -> CarHeaterReadyByState | None:
        """Get ready-by state (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_ready_by_state called")

        try:
            stmt = select(car_heater_ready_by_state).where(car_heater_ready_by_state.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No ready_by_state record found")
                return None

            return CarHeaterReadyByState(
                id=1,
                state_json=row["state_json"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting ready_by_state: %s", e)
            raise

    def save_ready_by_state(self, state: CarHeaterReadyByState) -> None:
        """Save ready-by state (upsert singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_ready_by_state called")

        try:
            # Use pg_insert with on_conflict_do_update for upsert behavior
            stmt = pg_insert(car_heater_ready_by_state).values(
                id=1,
                state_json=state.state_json,
                updated_ts=state.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "state_json": stmt.excluded.state_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving ready_by_state: %s", e)
            raise

    # --- Ready-by config (single-row settings) ---
    def get_ready_by_config(self) -> CarHeaterReadyByConfig | None:
        """Get ready-by config (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_ready_by_config called")

        try:
            stmt = select(car_heater_ready_by_config).where(car_heater_ready_by_config.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No ready_by_config record found")
                return None

            return CarHeaterReadyByConfig(
                id=1,
                config_json=row["config_json"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting ready_by_config: %s", e)
            raise

    def save_ready_by_config(self, config: CarHeaterReadyByConfig) -> None:
        """Save ready-by config (upsert singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_ready_by_config called")

        try:
            # Use pg_insert with on_conflict_do_update for upsert behavior
            stmt = pg_insert(car_heater_ready_by_config).values(
                id=1,
                config_json=config.config_json,
                updated_ts=config.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "config_json": stmt.excluded.config_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving ready_by_config: %s", e)
            raise

    def migrate_ready_by_to_pg(self, batch_size: int = 1000) -> dict[str, Any]:
        """Migrate car heater ready-by data from SQLite to PostgreSQL.

        Args:
            batch_size: Number of rows to insert per transaction (not used for singletons)

        Returns:
            Dictionary with migration statistics
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.info("=" * 60)
        logger.info("Starting ready-by data migration from SQLite to PostgreSQL")
        logger.info("Batch size: %d rows per transaction", batch_size)
        logger.info("=" * 60)

        stats: dict[str, Any] = {}

        # Migrate car_heater_ready_by_state (singleton)
        try:
            rows = self.db.fetchall(
                "SELECT id, state_json, updated_ts FROM car_heater_ready_by_state"
            )
            total = len(rows)
            logger.info("📊 ready_by_state: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ ready_by_state: No records to migrate")
                stats["ready_by_state"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0

                for row in rows:
                    try:
                        # Use pg_insert with on_conflict for idempotency
                        stmt = pg_insert(car_heater_ready_by_state).values(
                            id=row["id"],
                            state_json=row["state_json"],
                            updated_ts=row["updated_ts"],
                        )
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["id"],
                            set_={
                                "state_json": stmt.excluded.state_json,
                                "updated_ts": stmt.excluded.updated_ts,
                            },
                        )
                        with sa_engine.begin() as conn:
                            conn.execute(stmt)
                        migrated += 1
                    except Exception as e:
                        logger.error("❌ Error migrating ready_by_state row: %s", e)
                        errors += 1

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ ready_by_state: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["ready_by_state"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in ready_by_state migration: %s", e)
            stats["ready_by_state"] = {"migrated": 0, "errors": 1}

        # Migrate car_heater_ready_by_config (singleton)
        try:
            rows = self.db.fetchall(
                "SELECT id, config_json, updated_ts FROM car_heater_ready_by_config"
            )
            total = len(rows)
            logger.info("📊 ready_by_config: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ ready_by_config: No records to migrate")
                stats["ready_by_config"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0

                for row in rows:
                    try:
                        # Use pg_insert with on_conflict for idempotency
                        stmt = pg_insert(car_heater_ready_by_config).values(
                            id=row["id"],
                            config_json=row["config_json"],
                            updated_ts=row["updated_ts"],
                        )
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["id"],
                            set_={
                                "config_json": stmt.excluded.config_json,
                                "updated_ts": stmt.excluded.updated_ts,
                            },
                        )
                        with sa_engine.begin() as conn:
                            conn.execute(stmt)
                        migrated += 1
                    except Exception as e:
                        logger.error("❌ Error migrating ready_by_config row: %s", e)
                        errors += 1

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ ready_by_config: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["ready_by_config"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in ready_by_config migration: %s", e)
            stats["ready_by_config"] = {"migrated": 0, "errors": 1}

        # Summary
        logger.info("=" * 60)
        logger.info("✓ Ready-by data migration complete!")
        for table_name, table_stats in stats.items():
            logger.info(
                "  %s: %d migrated, %d errors",
                table_name,
                table_stats["migrated"],
                table_stats["errors"],
            )
        logger.info("=" * 60)

        return stats
