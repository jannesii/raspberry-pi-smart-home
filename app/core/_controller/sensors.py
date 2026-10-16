from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, Text, insert, select
from sqlalchemy.sql import func

from ..models import BMPData, ESP32TemperatureHumidity
from ..schema import bmp_sensor_data, esp32_temphum

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SensorsMixin:
    def record_esp32_temphum(
        self, location: str, temperature: float, humidity: float, ac_on: bool | None = None
    ) -> ESP32TemperatureHumidity:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

        try:
            stmt = (
                insert(esp32_temphum)
                .values(
                    location=location,
                    timestamp=now,
                    temperature=temperature,
                    humidity=humidity,
                    ac_on=ac_on,
                )
                .returning(
                    esp32_temphum.c.id,
                    esp32_temphum.c.location,
                    esp32_temphum.c.timestamp,
                    esp32_temphum.c.temperature,
                    esp32_temphum.c.humidity,
                    esp32_temphum.c.ac_on,
                )
            )

            with sa_engine.begin() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                raise RuntimeError("Failed to retrieve inserted esp32_temphum record")

            return ESP32TemperatureHumidity(
                id=row["id"],
                location=row["location"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                humidity=row["humidity"],
                ac_on=row["ac_on"],
            )
        except Exception as e:
            logger.exception("Error recording esp32_temphum: %s", e)
            raise

    def get_last_esp32_temphum(self) -> ESP32TemperatureHumidity | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(esp32_temphum).order_by(esp32_temphum.c.id.desc()).limit(1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return ESP32TemperatureHumidity(
                id=row["id"],
                location=row["location"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                humidity=row["humidity"],
                ac_on=row["ac_on"],
            )
        except Exception as e:
            logger.exception("Error fetching last esp32_temphum: %s", e)
            return None

    def get_esp32_temphum_for_date(
        self, date_str: str, location: str
    ) -> list[ESP32TemperatureHumidity]:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            # Cast date to text for PostgreSQL compatibility
            stmt = (
                select(esp32_temphum)
                .where(
                    func.cast(func.date(esp32_temphum.c.timestamp), Text) == date_str,
                    esp32_temphum.c.location == location,
                )
                .order_by(esp32_temphum.c.timestamp)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

            return [
                ESP32TemperatureHumidity(
                    id=row["id"],
                    location=row["location"],
                    timestamp=row["timestamp"],
                    temperature=row["temperature"],
                    humidity=row["humidity"],
                    ac_on=row["ac_on"],
                )
                for row in rows
            ]
        except Exception as e:
            logger.exception("Error fetching esp32_temphum for date: %s", e)
            return []

    def get_last_esp32_temphum_for_location(self, location: str) -> ESP32TemperatureHumidity | None:
        """Return the most recent ESP32TemperatureHumidity row for a given location, or None."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = (
                select(esp32_temphum)
                .where(esp32_temphum.c.location == location)
                .order_by(esp32_temphum.c.timestamp.desc(), esp32_temphum.c.id.desc())
                .limit(1)
            )

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return ESP32TemperatureHumidity(
                id=row["id"],
                location=row["location"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                humidity=row["humidity"],
                ac_on=row["ac_on"],
            )
        except Exception as e:
            logger.exception("Error fetching last esp32_temphum for location: %s", e)
            return None

    def get_unique_locations(self) -> list[dict[str, Any]]:
        """
        Return the latest (most recent) reading per unique location,
        as a list of dicts with keys: location, temp, hum.
        """
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            # Use window function with SQLAlchemy

            # PostgreSQL supports window functions directly
            # For SQLite fallback, we'll try window function first
            subq = select(
                esp32_temphum,
                func.row_number()
                .over(
                    partition_by=esp32_temphum.c.location,
                    order_by=(esp32_temphum.c.timestamp.desc(), esp32_temphum.c.id.desc()),
                )
                .label("rn"),
            ).subquery("ranked")

            stmt_window = (
                select(
                    subq.c.id,
                    subq.c.location,
                    subq.c.timestamp,
                    subq.c.temperature,
                    subq.c.humidity,
                )
                .where(subq.c.rn == 1)
                .order_by(subq.c.location)
            )

            with sa_engine.connect() as conn:
                try:
                    rows = conn.execute(stmt_window).mappings().all()
                except Exception:
                    # Fallback for older SQLite without window functions
                    # Use a correlated subquery
                    e2 = esp32_temphum.alias("e2")
                    subq_fallback = (
                        select(e2.c.id)
                        .where(e2.c.location == esp32_temphum.c.location)
                        .order_by(e2.c.timestamp.desc(), e2.c.id.desc())
                        .limit(1)
                        .scalar_subquery()
                    )
                    stmt_fallback = (
                        select(
                            esp32_temphum.c.id,
                            esp32_temphum.c.location,
                            esp32_temphum.c.timestamp,
                            esp32_temphum.c.temperature,
                            esp32_temphum.c.humidity,
                        )
                        .where(esp32_temphum.c.id == subq_fallback)
                        .order_by(esp32_temphum.c.location)
                    )
                    rows = conn.execute(stmt_fallback).mappings().all()

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
        except Exception as e:
            logger.exception("Error fetching unique locations: %s", e)
            return []

    def record_bmp_sensor_data(
        self, temperature: float, pressure: float, altitude: float
    ) -> BMPData:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

        try:
            stmt = (
                insert(bmp_sensor_data)
                .values(
                    timestamp=now,
                    temperature=temperature,
                    pressure=pressure,
                    altitude=altitude,
                )
                .returning(
                    bmp_sensor_data.c.id,
                    bmp_sensor_data.c.timestamp,
                    bmp_sensor_data.c.temperature,
                    bmp_sensor_data.c.pressure,
                    bmp_sensor_data.c.altitude,
                )
            )

            with sa_engine.begin() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                raise RuntimeError("Failed to retrieve inserted bmp_sensor_data record")

            return BMPData(
                id=row["id"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                pressure=row["pressure"],
                altitude=row["altitude"],
            )
        except Exception as e:
            logger.exception("Error recording bmp_sensor_data: %s", e)
            raise

    def get_last_bmp_sensor_data(self) -> BMPData | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(bmp_sensor_data).order_by(bmp_sensor_data.c.id.desc()).limit(1)

            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                return None

            return BMPData(
                id=row["id"],
                timestamp=row["timestamp"],
                temperature=row["temperature"],
                pressure=row["pressure"],
                altitude=row["altitude"],
            )
        except Exception as e:
            logger.exception("Error fetching last bmp_sensor_data: %s", e)
            return None

    def get_bmp_sensor_data_for_date(self, date_str: str) -> list[BMPData]:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            # Cast date to text for PostgreSQL compatibility
            stmt = (
                select(bmp_sensor_data)
                .where(func.cast(func.date(bmp_sensor_data.c.timestamp), Text) == date_str)
                .order_by(bmp_sensor_data.c.timestamp)
            )

            with sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()

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
        except Exception as e:
            logger.exception("Error fetching bmp_sensor_data for date: %s", e)
            return []
