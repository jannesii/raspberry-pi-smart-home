from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..schema import (
    ac_events,
    api_keys,
    car_heater_charge_mode,
    car_heater_keep_at_temp,
    car_heater_ready_by_config,
    car_heater_ready_by_state,
    car_heater_status,
    gcode_commands,
    images,
    metadata,
    status,
    thermostat_conf,
    timelapse_conf,
    users,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy import Table


class MigrationMixin:
    """Legacy SQLite to SQLAlchemy migration helpers used by tests and ops scripts."""

    def _require_sa_engine_for_migration(self):
        sa_engine = getattr(self, "_sa_engine", None)
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")
        return sa_engine

    def _connect_legacy_sqlite(self) -> sqlite3.Connection:
        db_path = getattr(self, "db_path", None)
        if not db_path:
            raise RuntimeError("Legacy SQLite DB path is not configured")
        logger.debug("Opening legacy SQLite connection for migrations db_path=%s", db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _legacy_table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        logger.debug("Checking legacy table existence table=%s", table_name)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _make_table_stats(self) -> dict[str, int]:
        return {"migrated": 0, "errors": 0}

    def _build_upsert_stmt(
        self,
        target_table: Table,
        row_data: dict[str, Any],
    ):
        sa_engine = self._require_sa_engine_for_migration()
        pk_columns = [column.name for column in target_table.primary_key.columns]
        if sa_engine.dialect.name == "postgresql":
            stmt = pg_insert(target_table).values(**row_data)
            update_values = {
                key: getattr(stmt.excluded, key) for key in row_data if key not in pk_columns
            }
            if update_values:
                return stmt.on_conflict_do_update(
                    index_elements=pk_columns,
                    set_=update_values,
                )
            return stmt.on_conflict_do_nothing(index_elements=pk_columns)

        if sa_engine.dialect.name == "sqlite":
            stmt = sqlite_insert(target_table).values(**row_data)
            update_values = {
                key: getattr(stmt.excluded, key) for key in row_data if key not in pk_columns
            }
            if update_values:
                return stmt.on_conflict_do_update(
                    index_elements=pk_columns,
                    set_=update_values,
                )
            return stmt.on_conflict_do_nothing(index_elements=pk_columns)

        return target_table.insert().values(**row_data)

    def _migrate_sqlite_table(
        self,
        *,
        source_table_name: str,
        target_table: Table,
        stats_key: str,
        transform: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        stats = self._make_table_stats()
        sa_engine = self._require_sa_engine_for_migration()
        metadata.create_all(sa_engine)
        logger.debug(
            "Migrating legacy table source=%s target=%s stats_key=%s",
            source_table_name,
            target_table.name,
            stats_key,
        )

        with closing(self._connect_legacy_sqlite()) as legacy_conn:
            if not self._legacy_table_exists(legacy_conn, source_table_name):
                logger.debug("Skipping migration for missing legacy table=%s", source_table_name)
                return stats

            rows = legacy_conn.execute(
                f"SELECT * FROM {source_table_name} ORDER BY rowid ASC"
            ).fetchall()
            logger.debug("Fetched %d legacy rows from %s", len(rows), source_table_name)

            with sa_engine.begin() as conn:
                for row in rows:
                    try:
                        mapped_row = dict(row)
                        row_data = transform(mapped_row) if transform else mapped_row
                        if not row_data:
                            logger.debug(
                                "Skipping empty mapped row during migration source=%s",
                                source_table_name,
                            )
                            continue
                        stmt = self._build_upsert_stmt(target_table, row_data)
                        conn.execute(stmt)
                        stats["migrated"] += 1
                    except Exception:
                        stats["errors"] += 1
                        logger.exception(
                            "Failed migrating row for source=%s stats_key=%s row=%s",
                            source_table_name,
                            stats_key,
                            dict(row),
                        )

        logger.debug(
            "Completed migration source=%s stats=%s",
            source_table_name,
            stats,
        )
        return stats

    def migrate_3d_to_pg(self, batch_size: int = 500) -> dict[str, dict[str, int]]:
        """Migrate 3D-printer related tables from legacy SQLite into SQLAlchemy."""
        logger.debug("migrate_3d_to_pg called batch_size=%s", batch_size)
        return {
            "status": self._migrate_sqlite_table(
                source_table_name="status",
                target_table=status,
                stats_key="status",
            ),
            "images": self._migrate_sqlite_table(
                source_table_name="images",
                target_table=images,
                stats_key="images",
            ),
            "timelapse_conf": self._migrate_sqlite_table(
                source_table_name="timelapse_conf",
                target_table=timelapse_conf,
                stats_key="timelapse_conf",
            ),
            "gcode_commands": self._migrate_sqlite_table(
                source_table_name="gcode_commands",
                target_table=gcode_commands,
                stats_key="gcode_commands",
            ),
        }

    def migrate_ac_to_pg(self, batch_size: int = 500) -> dict[str, dict[str, int]]:
        """Migrate AC and thermostat tables from legacy SQLite into SQLAlchemy."""
        logger.debug("migrate_ac_to_pg called batch_size=%s", batch_size)
        return {
            "ac_events": self._migrate_sqlite_table(
                source_table_name="ac_events",
                target_table=ac_events,
                stats_key="ac_events",
            ),
            "thermostat_conf": self._migrate_sqlite_table(
                source_table_name="thermostat_conf",
                target_table=thermostat_conf,
                stats_key="thermostat_conf",
            ),
        }

    def migrate_auth_to_pg(self, batch_size: int = 500) -> dict[str, dict[str, int]]:
        """Migrate auth tables from legacy SQLite into SQLAlchemy."""
        logger.debug("migrate_auth_to_pg called batch_size=%s", batch_size)
        return {
            "users": self._migrate_sqlite_table(
                source_table_name="users",
                target_table=users,
                stats_key="users",
            ),
            "api_keys": self._migrate_sqlite_table(
                source_table_name="api_keys",
                target_table=api_keys,
                stats_key="api_keys",
            ),
        }

    def migrate_car_heater_to_pg(self, batch_size: int = 500) -> dict[str, dict[str, int]]:
        """Migrate core car-heater tables from legacy SQLite into SQLAlchemy."""
        logger.debug("migrate_car_heater_to_pg called batch_size=%s", batch_size)
        return {
            "car_heater_status": self._migrate_sqlite_table(
                source_table_name="car_heater_status",
                target_table=car_heater_status,
                stats_key="car_heater_status",
            ),
            "car_heater_charge_mode": self._migrate_sqlite_table(
                source_table_name="car_heater_charge_mode",
                target_table=car_heater_charge_mode,
                stats_key="car_heater_charge_mode",
            ),
            "car_heater_keep_at_temp": self._migrate_sqlite_table(
                source_table_name="car_heater_keep_at_temp",
                target_table=car_heater_keep_at_temp,
                stats_key="car_heater_keep_at_temp",
            ),
        }

    def migrate_ready_by_to_pg(self, batch_size: int = 500) -> dict[str, dict[str, int]]:
        """Migrate car-heater ready-by tables from legacy SQLite into SQLAlchemy."""
        logger.debug("migrate_ready_by_to_pg called batch_size=%s", batch_size)
        return {
            "ready_by_state": self._migrate_sqlite_table(
                source_table_name="car_heater_ready_by_state",
                target_table=car_heater_ready_by_state,
                stats_key="ready_by_state",
            ),
            "ready_by_config": self._migrate_sqlite_table(
                source_table_name="car_heater_ready_by_config",
                target_table=car_heater_ready_by_config,
                stats_key="ready_by_config",
            ),
        }
