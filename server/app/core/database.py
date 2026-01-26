"""Database manager for SQLite connections and schema."""

import contextlib
import logging
import sqlite3
import threading
from sqlite3 import Connection, Cursor
from typing import Any

logger = logging.getLogger(__name__)


class DatabaseManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: str):
        logger.debug("DatabaseManager.__new__ called db_path=%s", db_path)
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: str):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        logger.debug("DatabaseManager.__init__ called db_path=%s", db_path)
        self.db_path = db_path
        self.conn: Connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            logger.debug("DatabaseManager enabling SQLite foreign_keys PRAGMA")
            self.conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            logger.debug("DatabaseManager failed to enable foreign_keys PRAGMA", exc_info=True)
        self.cursor: Cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self) -> None:
        logger.debug("DatabaseManager._create_tables called")
        # Ensure all required tables exist
        tables = {
            "users": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "username TEXT UNIQUE NOT NULL, "
                "password_hash TEXT NOT NULL, "
                "is_admin BOOLEAN NOT NULL DEFAULT FALSE, "
                "is_root_admin BOOLEAN NOT NULL DEFAULT 0, "
                "is_temporary BOOLEAN DEFAULT 0, "
                "expires_at TEXT"
            ),
            "api_keys": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "key_id TEXT UNIQUE NOT NULL, "
                "name TEXT NOT NULL, "
                "secret_hash TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "created_by TEXT, "
                "revoked BOOLEAN NOT NULL DEFAULT 0, "
                "last_used_at TEXT"
            ),
            "esp32_temphum": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "location TEXT NOT NULL, "
                "timestamp TEXT NOT NULL, "
                "temperature REAL NOT NULL, "
                "humidity REAL NOT NULL, "
                "ac_on BOOLEAN"
            ),
            "ac_events": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, "
                "is_on BOOLEAN NOT NULL, "
                "source TEXT, "
                "note TEXT"
            ),
            "status": (
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "timestamp TEXT NOT NULL, "
                "status TEXT NOT NULL"
            ),
            "images": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, image TEXT NOT NULL"
            ),
            "timelapse_conf": (
                "id INTEGER PRIMARY KEY CHECK (id = 1),"
                "image_delay INTEGER NOT NULL,"
                "temphum_delay INTEGER NOT NULL,"
                "status_delay INTEGER NOT NULL"
            ),
            "thermostat_conf": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "sleep_active BOOLEAN NOT NULL, "
                "sleep_start TEXT, "
                "sleep_stop TEXT, "
                "target_temp REAL NOT NULL, "
                "pos_hysteresis REAL NOT NULL, "
                "neg_hysteresis REAL NOT NULL, "
                "thermo_active BOOLEAN NOT NULL DEFAULT 1, "
                "total_on_s INTEGER NOT NULL DEFAULT 0, "
                "total_off_s INTEGER NOT NULL DEFAULT 0, "
                "min_on_s INTEGER NOT NULL DEFAULT 240, "
                "min_off_s INTEGER NOT NULL DEFAULT 240, "
                "poll_interval_s INTEGER NOT NULL DEFAULT 15, "
                "smooth_window INTEGER NOT NULL DEFAULT 5, "
                "max_stale_s INTEGER"
            ),
            "gcode_commands": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, gcode TEXT NOT NULL"
            ),
            "logs": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, "
                "type TEXT NOT NULL, "
                "message TEXT NOT NULL"
            ),
            "bmp_sensor_data": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, "
                "temperature REAL NOT NULL, "
                "pressure REAL NOT NULL, "
                "altitude REAL NOT NULL"
            ),
            "car_heater_status": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, "
                "is_heater_on BOOLEAN NOT NULL, "
                "instant_power_w REAL NOT NULL, "
                "voltage_v REAL, "
                "current_a REAL, "
                "energy_total_wh REAL, "
                "energy_last_min_wh REAL, "
                "energy_ts INTEGER, "
                "device_temp_c REAL, "
                "device_temp_f REAL, "
                "ambient_temp REAL, "
                "source TEXT"
            ),
            "car_heater_charge_mode": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "enabled BOOLEAN NOT NULL DEFAULT 0, "
                "threshold_w REAL NOT NULL DEFAULT 20.0, "
                "power_cut BOOLEAN NOT NULL DEFAULT 0, "
                "power_cut_at TEXT, "
                "last_instant_power_w REAL, "
                "seen_above_threshold BOOLEAN NOT NULL DEFAULT 0"
            ),
            "car_heater_keep_at_temp": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "target_temperature_c REAL, "
                "hysteresis_c REAL, "
                "enabled BOOLEAN NOT NULL DEFAULT 0"
            ),
            "car_heater_kfactor_session": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "start_ts TEXT NOT NULL, "
                "end_ts TEXT NOT NULL, "
                "auto_window_date TEXT, "
                "auto_window_start TEXT, "
                "auto_window_stop TEXT, "
                "heater_mode TEXT, "
                "outside_t_mean REAL, "
                "outside_t_min REAL, "
                "outside_t_max REAL, "
                "wind_mean REAL, "
                "cabin_t_start REAL, "
                "cabin_t_end REAL, "
                "cabin_t_max REAL, "
                "duration_s INTEGER, "
                "sample_count INTEGER, "
                "flags_json TEXT, "
                "quality_score REAL, "
                "accepted BOOLEAN NOT NULL DEFAULT 0"
            ),
            "car_heater_kfactor_result": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id INTEGER, "
                "model_version TEXT, "
                "k_loss_W_per_K REAL, "
                "eta REAL, "
                "rmse_C REAL, "
                "r2 REAL, "
                "confidence REAL, "
                "promoted BOOLEAN NOT NULL DEFAULT 0, "
                "created_ts TEXT"
            ),
            "car_heater_kfactor_prediction_outcome": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "predicted_minutes REAL, "
                "actual_minutes REAL, "
                "error_minutes REAL, "
                "cabin_start_c REAL, "
                "cabin_end_c REAL, "
                "target_c REAL, "
                "outside_c REAL, "
                "created_ts TEXT"
            ),
            "car_heater_kfactor_active_params": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "k_loss_W_per_K REAL, "
                "eta REAL, "
                "updated_ts TEXT, "
                "source TEXT"
            ),
            "car_heater_kfactor_bucket_params": (
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "t_bucket INTEGER, "
                "wind_bucket INTEGER NOT NULL, "
                "k_loss_W_per_K REAL, "
                "eta REAL, "
                "updated_ts TEXT, "
                "source TEXT, "
                "UNIQUE(t_bucket, wind_bucket)"
            ),
            "car_heater_kfactor_config": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), config_json TEXT, updated_ts TEXT"
            ),
            "car_heater_kfactor_cooldown": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), cooldown_until TEXT, updated_ts TEXT"
            ),
            "car_heater_ready_by_state": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), state_json TEXT, updated_ts TEXT"
            ),
            "car_heater_ready_by_config": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), config_json TEXT, updated_ts TEXT"
            ),
            "logging_control": (
                "id INTEGER PRIMARY KEY CHECK (id = 1), config_json TEXT, updated_ts TEXT"
            ),
        }
        logger.debug("DatabaseManager._create_tables ensuring kfactor prediction outcome table")
        for name, schema in tables.items():
            self.cursor.execute(f"CREATE TABLE IF NOT EXISTS {name} ({schema})")

        # Clean up test data if present
        self.cursor.execute("DELETE FROM esp32_temphum WHERE location='Test' OR location='test'")

        # Insert default values for status and timelapse_conf if they don't exist
        self.cursor.execute("""
        INSERT OR IGNORE INTO status (id, timestamp, status)
        VALUES (1, datetime('now'), 'IDLE')
        """)

        self.cursor.execute("""
        INSERT OR IGNORE INTO timelapse_conf (id, image_delay, temphum_delay, status_delay)
        VALUES (1, 5, 10, 15)
        """)

        triggers = {
            "keep_only_last_10_images": """
            CREATE TRIGGER IF NOT EXISTS keep_only_last_10_images
            AFTER INSERT ON images
            FOR EACH ROW
            BEGIN
                DELETE FROM images
                WHERE id NOT IN (
                    SELECT id
                    FROM images
                    ORDER BY timestamp DESC
                    LIMIT 10
                );
            END;
            """,
            "cleanup_esp32_temphum_after_insert": """
            CREATE TRIGGER IF NOT EXISTS cleanup_esp32_temphum_after_insert
            AFTER INSERT ON esp32_temphum
            BEGIN
                DELETE FROM esp32_temphum
                WHERE timestamp < datetime('now', '-30 days');
            END;
            """,
            "cleanup_ac_events_after_insert": """
            CREATE TRIGGER IF NOT EXISTS cleanup_ac_events_after_insert
            AFTER INSERT ON ac_events
            BEGIN
                DELETE FROM ac_events
                WHERE timestamp < datetime('now', '-30 days');
            END;
            """,
            "cleanup_bmp_sensor_data_after_insert": """
            CREATE TRIGGER IF NOT EXISTS cleanup_bmp_sensor_data_after_insert
            AFTER INSERT ON bmp_sensor_data
            BEGIN
                DELETE FROM bmp_sensor_data
                WHERE timestamp < datetime('now', '-7 days');
            END;
            """,
            "cleanup_car_heater_status_after_insert": """
            CREATE TRIGGER IF NOT EXISTS cleanup_car_heater_status_after_insert
            AFTER INSERT ON car_heater_status
            BEGIN
                DELETE FROM car_heater_status
                WHERE timestamp < datetime('now', '-30 days');
            END;
            """,
        }

        for _name, trigger_sql in triggers.items():
            self.cursor.executescript(trigger_sql)

        # --- Indexes for performance-critical queries ---
        # Speed up latest-per-location lookups used by Controller.get_unique_locations()
        # Pattern: WHERE location = ? ORDER BY timestamp DESC, id DESC LIMIT 1
        # This composite index allows an efficient seek to the newest row per location.
        try:
            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_esp32_temphum_loc_ts_id
                ON esp32_temphum (location, timestamp DESC, id DESC)
                """
            )
        except Exception:
            # Best-effort: ignore if SQLite version doesn't support DESC in index columns
            with contextlib.suppress(Exception):
                self.cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_esp32_temphum_loc_ts_id ON esp32_temphum (location, timestamp, id)"
                )

        # Optional: help date-based reads per location
        # Pattern: WHERE date(timestamp) = ? AND location = ?
        # Expression indexes are supported by SQLite; if not, ignore.
        with contextlib.suppress(Exception):
            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_esp32_temphum_date_loc
                ON esp32_temphum (date(timestamp), location)
                """
            )

        # Indexes for ac_events
        with contextlib.suppress(Exception):
            self.cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ac_events_ts ON ac_events (timestamp)"
            )

        # Indexes for API keys
        with contextlib.suppress(Exception):
            self.cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_key_id ON api_keys (key_id)"
            )

        # Try to add weekly sleep column for thermostat_conf if missing
        with contextlib.suppress(Exception):
            self.cursor.execute("ALTER TABLE thermostat_conf ADD COLUMN sleep_weekly TEXT")
        with contextlib.suppress(Exception):
            self.cursor.execute("ALTER TABLE thermostat_conf ADD COLUMN control_locations TEXT")

        self.conn.commit()
        logger.debug("DatabaseManager._create_tables completed")

    def execute_query(self, query: str, params: tuple[Any, ...] = ()) -> Cursor:
        self.cursor.execute(query, params)
        self.conn.commit()
        return self.cursor

    def executemany(self, query: str, param_list: list[tuple[Any, ...]]) -> None:
        self.cursor.executemany(query, param_list)
        self.conn.commit()

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        self.cursor.execute(query, params)
        return self.cursor.fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        self.cursor.execute(query, params)
        return self.cursor.fetchall()
