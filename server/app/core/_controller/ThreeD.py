from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import ImageData, Status, TimelapseConf
from ..schema import gcode_commands, images, status, timelapse_conf

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ThreeDMixin:
    def update_3d_status(self, status_val: str) -> Status:
        """Update 3D printer status (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("update_3d_status called with status=%s", status_val)
        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

        try:
            # Update singleton status record
            stmt = update(status).where(status.c.id == 1).values(timestamp=now, status=status_val)
            with sa_engine.begin() as conn:
                conn.execute(stmt)

            # Fetch updated record
            stmt_sel = select(status).where(status.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt_sel).mappings().first()

            if row is None:
                raise RuntimeError("Failed to retrieve status record")

            return Status(id=row["id"], timestamp=row["timestamp"], status=row["status"])
        except Exception as e:
            logger.exception("Error updating 3D status: %s", e)
            raise

    def get_last_3d_status(self) -> Status | None:
        """Get current 3D printer status (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_last_3d_status called")

        try:
            stmt = select(status).where(status.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No status record found")
                return None

            return Status(id=row["id"], timestamp=row["timestamp"], status=row["status"])
        except Exception as e:
            logger.exception("Error getting 3D status: %s", e)
            raise

    def record_image(self, image_base64: str) -> ImageData:
        """Record a new 3D printer camera image."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("record_image called with image length=%d", len(image_base64))
        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

        try:
            stmt = insert(images).values(timestamp=now, image=image_base64)
            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
                new_id = result.lastrowid

            # Fetch the inserted record
            stmt_sel = select(images).where(images.c.id == new_id)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt_sel).mappings().first()

            if row is None:
                raise RuntimeError("Failed to retrieve inserted image record")

            return ImageData(id=row["id"], timestamp=row["timestamp"], image=row["image"])
        except Exception as e:
            logger.exception("Error recording image: %s", e)
            raise

    def get_last_image(self) -> ImageData | None:
        """Get the most recent 3D printer camera image."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_last_image called")

        try:
            stmt = select(images).order_by(images.c.id.desc()).limit(1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No images found")
                return None

            return ImageData(id=row["id"], timestamp=row["timestamp"], image=row["image"])
        except Exception as e:
            logger.exception("Error getting last image: %s", e)
            raise

    def get_timelapse_conf(self) -> TimelapseConf | None:
        """Get timelapse configuration (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_timelapse_conf called")

        try:
            stmt = select(timelapse_conf).where(timelapse_conf.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No timelapse_conf record found")
                return None

            return TimelapseConf(
                id=row["id"],
                image_delay=row["image_delay"],
                temphum_delay=row["temphum_delay"],
                status_delay=row["status_delay"],
            )
        except Exception as e:
            logger.exception("Error getting timelapse config: %s", e)
            raise

    def update_timelapse_conf(
        self, image_delay: int, temphum_delay: int, status_delay: int
    ) -> None:
        """Update timelapse configuration (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug(
            "update_timelapse_conf called: image_delay=%d, temphum_delay=%d, status_delay=%d",
            image_delay,
            temphum_delay,
            status_delay,
        )

        try:
            stmt = (
                update(timelapse_conf)
                .where(timelapse_conf.c.id == 1)
                .values(
                    image_delay=image_delay,
                    temphum_delay=temphum_delay,
                    status_delay=status_delay,
                )
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error updating timelapse config: %s", e)
            raise

    def record_gcode_command(self, gcode: str) -> None:
        """Record a G-code command sent to the 3D printer."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("Recording G-code command: %s", gcode)
        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

        try:
            stmt = insert(gcode_commands).values(timestamp=now, gcode=gcode)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error recording gcode command: %s", e)
            raise

    def get_all_gcode_commands(self) -> list[str]:
        """Get all unique G-code commands (most recent first)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_all_gcode_commands called")

        try:
            stmt = select(gcode_commands.c.gcode).order_by(gcode_commands.c.id.desc())
            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            gcode_set = {row["gcode"] for row in rows}
            logger.debug("get_all_gcode_commands returning %d commands", len(gcode_set))
            return list(gcode_set)
        except Exception as e:
            logger.exception("Error getting gcode commands: %s", e)
            raise

    def migrate_3d_to_pg(self, batch_size: int = 1000) -> dict[str, Any]:
        """Migrate 3D printer data from SQLite to PostgreSQL.

        Args:
            batch_size: Number of rows to insert per transaction

        Returns:
            Dictionary with migration statistics
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.info("=" * 60)
        logger.info("Starting 3D data migration from SQLite to PostgreSQL")
        logger.info("Batch size: %d rows per transaction", batch_size)
        logger.info("=" * 60)

        stats: dict[str, Any] = {}

        # Migrate status (singleton)
        try:
            rows = self.db.fetchall("SELECT id, timestamp, status FROM status")
            total = len(rows)
            logger.info("📊 status: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ status: No records to migrate")
                stats["status"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0

                for row in rows:
                    try:
                        # Use pg_insert with on_conflict_do_nothing for idempotency
                        stmt = pg_insert(status).values(
                            id=row["id"],
                            timestamp=row["timestamp"],
                            status=row["status"],
                        )
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                        with sa_engine.begin() as conn:
                            conn.execute(stmt)
                        migrated += 1
                    except Exception as e:
                        logger.error("❌ Error migrating status row: %s", e)
                        errors += 1

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ status: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["status"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in status migration: %s", e)
            stats["status"] = {"migrated": 0, "errors": 1}

        # Migrate images
        try:
            rows = self.db.fetchall("SELECT id, timestamp, image FROM images ORDER BY id")
            total = len(rows)
            logger.info("📊 images: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ images: No records to migrate")
                stats["images"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0
                batch = []

                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"],
                            "image": row["image"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            with sa_engine.begin() as conn:
                                conn.execute(insert(images), batch)
                            migrated += len(batch)
                            elapsed = time.time() - start_time
                            rate = migrated / elapsed if elapsed > 0 else 0
                            pct = (migrated / total) * 100
                            eta = (total - migrated) / rate if rate > 0 else 0
                            logger.info(
                                "📈 images: %d/%d (%.1f%%) | %.0f rows/sec | ETA: %.0fs",
                                migrated,
                                total,
                                pct,
                                rate,
                                eta,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating images batch: %s", e)
                            errors += len(batch)
                            batch = []

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ images: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["images"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in images migration: %s", e)
            stats["images"] = {"migrated": 0, "errors": 1}

        # Migrate timelapse_conf (singleton)
        try:
            rows = self.db.fetchall(
                "SELECT id, image_delay, temphum_delay, status_delay FROM timelapse_conf"
            )
            total = len(rows)
            logger.info("📊 timelapse_conf: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ timelapse_conf: No records to migrate")
                stats["timelapse_conf"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0

                for row in rows:
                    try:
                        stmt = pg_insert(timelapse_conf).values(
                            id=row["id"],
                            image_delay=row["image_delay"],
                            temphum_delay=row["temphum_delay"],
                            status_delay=row["status_delay"],
                        )
                        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                        with sa_engine.begin() as conn:
                            conn.execute(stmt)
                        migrated += 1
                    except Exception as e:
                        logger.error("❌ Error migrating timelapse_conf row: %s", e)
                        errors += 1

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ timelapse_conf: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["timelapse_conf"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in timelapse_conf migration: %s", e)
            stats["timelapse_conf"] = {"migrated": 0, "errors": 1}

        # Migrate gcode_commands
        try:
            rows = self.db.fetchall("SELECT id, timestamp, gcode FROM gcode_commands ORDER BY id")
            total = len(rows)
            logger.info("📊 gcode_commands: Found %d records to migrate", total)

            if total == 0:
                logger.info("✓ gcode_commands: No records to migrate")
                stats["gcode_commands"] = {"migrated": 0, "errors": 0}
            else:
                start_time = time.time()
                migrated = 0
                errors = 0
                batch = []

                for i, row in enumerate(rows, 1):
                    batch.append(
                        {
                            "id": row["id"],
                            "timestamp": row["timestamp"],
                            "gcode": row["gcode"],
                        }
                    )

                    if len(batch) >= batch_size or i == total:
                        try:
                            with sa_engine.begin() as conn:
                                conn.execute(insert(gcode_commands), batch)
                            migrated += len(batch)
                            elapsed = time.time() - start_time
                            rate = migrated / elapsed if elapsed > 0 else 0
                            pct = (migrated / total) * 100
                            eta = (total - migrated) / rate if rate > 0 else 0
                            logger.info(
                                "📈 gcode_commands: %d/%d (%.1f%%) | %.0f rows/sec | ETA: %.0fs",
                                migrated,
                                total,
                                pct,
                                rate,
                                eta,
                            )
                            batch = []
                        except Exception as e:
                            logger.error("❌ Error migrating gcode_commands batch: %s", e)
                            errors += len(batch)
                            batch = []

                elapsed = time.time() - start_time
                rate = migrated / elapsed if elapsed > 0 else 0
                logger.info("✓ gcode_commands: Completed in %.1fs (%.0f rows/sec)", elapsed, rate)
                stats["gcode_commands"] = {"migrated": migrated, "errors": errors}
        except Exception as e:
            logger.exception("❌ Error in gcode_commands migration: %s", e)
            stats["gcode_commands"] = {"migrated": 0, "errors": 1}

        # Summary
        logger.info("=" * 60)
        logger.info("✓ 3D data migration complete!")
        for table_name, table_stats in stats.items():
            logger.info(
                "  %s: %d migrated, %d errors",
                table_name,
                table_stats["migrated"],
                table_stats["errors"],
            )
        logger.info("=" * 60)

        return stats
