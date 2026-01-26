import logging
from datetime import datetime

from ..models import ImageData, Status, TimelapseConf

logger = logging.getLogger(__name__)


class ThreeDMixin:
    def update_3d_status(self, status: str) -> Status:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query("UPDATE status SET timestamp = ?, status = ?", (now, status))
        row = self.db.fetchone("SELECT id, timestamp, status FROM status")
        if row is None:
            raise RuntimeError("Failed to retrieve inserted status record")
        return Status(id=row["id"], timestamp=row["timestamp"], status=row["status"])

    def get_last_3d_status(self) -> Status | None:
        row = self.db.fetchone("SELECT id, timestamp, status FROM status")
        if row is None:
            return None
        return Status(id=row["id"], timestamp=row["timestamp"], status=row["status"])

    def record_image(self, image_base64: str) -> ImageData:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO images (timestamp, image) VALUES (?, ?)", (now, image_base64)
        )
        row = self.db.fetchone("SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1")
        if row is None:
            raise RuntimeError("Failed to retrieve inserted image record")
        return ImageData(id=row["id"], timestamp=row["timestamp"], image=row["image"])

    def get_last_image(self) -> ImageData | None:
        row = self.db.fetchone("SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1")
        if row is None:
            return None
        return ImageData(id=row["id"], timestamp=row["timestamp"], image=row["image"])

    def get_timelapse_conf(self) -> TimelapseConf | None:
        row = self.db.fetchone(
            "SELECT id, image_delay, temphum_delay, status_delay FROM timelapse_conf"
        )
        if row is None:
            return None
        return TimelapseConf(
            id=row["id"],
            image_delay=row["image_delay"],
            temphum_delay=row["temphum_delay"],
            status_delay=row["status_delay"],
        )

    def update_timelapse_conf(
        self, image_delay: int, temphum_delay: int, status_delay: int
    ) -> None:
        self.db.execute_query(
            """
            UPDATE timelapse_conf
               SET image_delay = ?,
                   temphum_delay = ?,
                   status_delay = ?
            """,
            (image_delay, temphum_delay, status_delay),
        )

    def record_gcode_command(self, gcode: str) -> None:
        logger.debug(f"Recording G-code command: {gcode}")
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO gcode_commands (timestamp, gcode) VALUES (?, ?)", (now, gcode)
        )

    def get_all_gcode_commands(self) -> list[str]:
        logger.debug("get_all_gcode_commands called")
        rows = self.db.fetchall("SELECT gcode FROM gcode_commands ORDER BY id DESC")
        gcode_set = {row["gcode"] for row in rows}
        logger.debug("get_all_gcode_commands returning %d commands", len(gcode_set))
        return list(gcode_set)
