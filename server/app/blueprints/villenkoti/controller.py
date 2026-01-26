from __future__ import annotations

import datetime
import hashlib
import os
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


class VillenkotiController:
    """Manages the Villenkoti-specific database, sensor readings and API keys."""

    def __init__(self, db_path: str | None = None):
        if db_path:
            resolved_path = db_path
        else:
            resolved_path = os.environ.get("VILLENKOTI_DB_PATH")
        if not resolved_path:
            project_dir = os.path.dirname(__file__)
            resolved_path = os.path.join(project_dir, "villenkoti.db")
        self.db_path = resolved_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                secret_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                last_used_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                location TEXT,
                temperature REAL,
                humidity REAL,
                metadata TEXT
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS cleanup_sensor_readings_after_insert
            AFTER INSERT ON sensor_readings
            BEGIN
                DELETE FROM sensor_readings
                WHERE timestamp < datetime('now', '-30 days');
            END;
            """,
            """
            DELETE FROM sensor_readings WHERE location='Test' OR location='test'
            """,
        ]
        with self._conn:
            for statement in statements:
                self._conn.execute(statement)

    def _hash_key(self, api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def store_api_key(self, name: str, secret: str, created_by: str | None = None) -> int:
        """Persists a new API key (plain secret will be hashed)."""
        hashed = self._hash_key(secret)
        created_at = datetime.datetime.utcnow().isoformat() + "Z"
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO api_keys (name, secret_hash, created_at, created_by) VALUES (?, ?, ?, ?)",
                (name, hashed, created_at, created_by),
            )
        return cursor.lastrowid

    def verify_api_key(self, secret: str) -> bool:
        """Returns True if the provided key matches one stored in this database."""
        if not secret:
            return False
        hashed = self._hash_key(secret)
        row = self._conn.execute(
            "SELECT id FROM api_keys WHERE secret_hash = ?",
            (hashed,),
        ).fetchone()
        if not row:
            return False
        now = datetime.datetime.utcnow().isoformat() + "Z"
        with self._conn:
            self._conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        return True

    def record_sensor_reading(
        self,
        *,
        location: str | None,
        temperature: float | None,
        humidity: float | None,
    ) -> int:
        """Stores a single sensor reading."""
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO sensor_readings (timestamp, location, temperature, humidity)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, location, temperature, humidity),
            )
        return cursor.lastrowid

    def execute_sql(
        self,
        statement: str,
        parameters: Iterable[Any] | None = None,
    ) -> sqlite3.Cursor:
        """Runs arbitrary SQL against the Villenkoti database."""
        cursor = self._conn.cursor()
        if parameters is None:
            cursor.execute(statement)
        elif isinstance(parameters, dict):
            cursor.execute(statement, parameters)
        else:
            cursor.execute(statement, tuple(parameters))
        self._conn.commit()
        return cursor
