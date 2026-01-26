import logging
from datetime import datetime
from typing import Any

from ..models import BMPData, ESP32TemperatureHumidity

logger = logging.getLogger(__name__)


class SensorsMixin:
    def record_esp32_temphum(
        self, location: str, temperature: float, humidity: float, ac_on: bool | None = None
    ) -> ESP32TemperatureHumidity:
        now = datetime.now(self.finland_tz).isoformat()
        # Insert with optional AC state flag (nullable)
        self.db.execute_query(
            "INSERT INTO esp32_temphum (location, timestamp, temperature, humidity, ac_on) VALUES (?, ?, ?, ?, ?)",
            (location, now, temperature, humidity, None if ac_on is None else (1 if ac_on else 0)),
        )
        row = self.db.fetchone(
            "SELECT id, location, timestamp, temperature, humidity, ac_on FROM esp32_temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted esp32_temphum record")
        return ESP32TemperatureHumidity(
            id=row["id"],
            location=row["location"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            humidity=row["humidity"],
            ac_on=(None if row["ac_on"] is None else bool(row["ac_on"])),
        )

    def get_last_esp32_temphum(self) -> ESP32TemperatureHumidity | None:
        row = self.db.fetchone(
            "SELECT id, location, timestamp, temperature, humidity, ac_on FROM esp32_temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return ESP32TemperatureHumidity(
            id=row["id"],
            location=row["location"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            humidity=row["humidity"],
            ac_on=(None if row["ac_on"] is None else bool(row["ac_on"])),
        )

    def get_esp32_temphum_for_date(
        self, date_str: str, location: str
    ) -> list[ESP32TemperatureHumidity]:
        rows = self.db.fetchall(
            """
            SELECT id, location, timestamp, temperature, humidity, ac_on
              FROM esp32_temphum
             WHERE date(timestamp) = ? AND location = ?
             ORDER BY timestamp
            """,
            (date_str, location),
        )
        return [
            ESP32TemperatureHumidity(
                id=row["id"],
                location=row["location"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                humidity=row["humidity"],
                ac_on=(None if row["ac_on"] is None else bool(row["ac_on"])),
            )
            for row in rows
        ]

    def get_last_esp32_temphum_for_location(self, location: str) -> ESP32TemperatureHumidity | None:
        """Return the most recent ESP32TemperatureHumidity row for a given location, or None."""
        row = self.db.fetchone(
            """
            SELECT id, location, timestamp, temperature, humidity, ac_on
              FROM esp32_temphum
             WHERE location = ?
             ORDER BY timestamp DESC, id DESC
             LIMIT 1
            """,
            (location,),
        )
        if row is None:
            return None
        return ESP32TemperatureHumidity(
            id=row["id"],
            location=row["location"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            humidity=row["humidity"],
            ac_on=(None if row["ac_on"] is None else bool(row["ac_on"])),
        )

    def get_unique_locations(self) -> list[dict[str, Any]]:
        """
        Return the latest (most recent) reading per unique location,
        as a list of dicts with keys: location, temp, hum.
        """
        try:
            # Fast path: use a window function to rank rows per location
            rows = self.db.fetchall(
                """
                SELECT id, location, timestamp, temperature, humidity
                FROM (
                  SELECT e.*, ROW_NUMBER() OVER (
                              PARTITION BY location
                              ORDER BY timestamp DESC, id DESC
                            ) AS rn
                  FROM esp32_temphum AS e
                )
                WHERE rn = 1
                ORDER BY location
                """
            )
        except Exception:
            # Fallback for older SQLite versions without window functions
            rows = self.db.fetchall(
                """
                SELECT e.id, e.location, e.timestamp, e.temperature, e.humidity
                FROM esp32_temphum AS e
                WHERE e.id = (
                    SELECT e2.id
                      FROM esp32_temphum AS e2
                     WHERE e2.location = e.location
                     ORDER BY e2.timestamp DESC, e2.id DESC
                     LIMIT 1
                )
                ORDER BY e.location
                """
            )

        return [
            {
                "location": row["location"],
                "temperature": float(row["temperature"])
                if row["temperature"] is not None
                else None,
                "humidity": float(row["humidity"]) if row["humidity"] is not None else None,
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

    def record_bmp_sensor_data(
        self, temperature: float, pressure: float, altitude: float
    ) -> BMPData:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO bmp_sensor_data (timestamp, temperature, pressure, altitude) VALUES (?, ?, ?, ?)",
            (now, temperature, pressure, altitude),
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, pressure, altitude FROM bmp_sensor_data ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted bmp_sensor_data record")
        return BMPData(
            id=row["id"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            pressure=row["pressure"],
            altitude=row["altitude"],
        )

    def get_last_bmp_sensor_data(self) -> BMPData | None:
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, pressure, altitude FROM bmp_sensor_data ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return BMPData(
            id=row["id"],
            timestamp=row["timestamp"],
            temperature=row["temperature"],
            pressure=row["pressure"],
            altitude=row["altitude"],
        )

    def get_bmp_sensor_data_for_date(self, date_str: str) -> list[BMPData]:
        rows = self.db.fetchall(
            """
            SELECT id, timestamp, temperature, pressure, altitude
              FROM bmp_sensor_data
             WHERE date(timestamp) = ?
             ORDER BY timestamp
            """,
            (date_str,),
        )
        return [
            BMPData(
                id=row["id"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                pressure=row["pressure"],
                altitude=row["altitude"],
            )
            for row in rows
        ]
