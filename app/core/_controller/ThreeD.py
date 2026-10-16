from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import Engine, insert, select, update

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
