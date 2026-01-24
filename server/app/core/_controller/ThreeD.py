from typing import Optional, List
from datetime import datetime
import logging

from .. import (
    Status,
    ImageData,
    TimelapseConf,
)

logger = logging.getLogger(__name__)


class ThreeDMixin:
    def update_3d_status(self, status: str) -> Status:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "UPDATE status SET timestamp = ?, status = ?",
            (now, status)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, status FROM status"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted status record")
        return Status(id=row['id'], timestamp=row['timestamp'], status=row['status'])

    def get_last_3d_status(self) -> Optional[Status]:
        row = self.db.fetchone(
            "SELECT id, timestamp, status FROM status"
        )
        if row is None:
            return None
        return Status(id=row['id'], timestamp=row['timestamp'], status=row['status'])

    def record_image(self, image_base64: str) -> ImageData:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO images (timestamp, image) VALUES (?, ?)",
            (now, image_base64)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted image record")
        return ImageData(id=row['id'], timestamp=row['timestamp'], image=row['image'])

    def get_last_image(self) -> Optional[ImageData]:
        row = self.db.fetchone(
            "SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return ImageData(id=row['id'], timestamp=row['timestamp'], image=row['image'])

    def get_timelapse_conf(self) -> Optional[TimelapseConf]:
        row = self.db.fetchone(
            "SELECT id, image_delay, temphum_delay, status_delay FROM timelapse_conf"
        )
        if row is None:
            return None
        return TimelapseConf(
            id=row['id'],
            image_delay=row['image_delay'],
            temphum_delay=row['temphum_delay'],
            status_delay=row['status_delay']
        )

    def update_timelapse_conf(self, image_delay: int, temphum_delay: int, status_delay: int) -> None:
        self.db.execute_query(
            """
            UPDATE timelapse_conf
               SET image_delay = ?,
                   temphum_delay = ?,
                   status_delay = ?
            """,
            (image_delay, temphum_delay, status_delay)
        )

    def record_gcode_command(self, gcode: str) -> None:
        logger.debug(f"Recording G-code command: {gcode}")
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO gcode_commands (timestamp, gcode) VALUES (?, ?)",
            (now, gcode)
        )

    def get_all_gcode_commands(self) -> List[str]:
        rows = self.db.fetchall(
            "SELECT gcode FROM gcode_commands ORDER BY id DESC"
        )
        gcode_set = set([row['gcode'] for row in rows])
        return list(gcode_set)
