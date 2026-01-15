# controller.py

import os
import tempfile
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from flask_login import current_user
from werkzeug.security import generate_password_hash, check_password_hash
import logging

from . import (
    User,
    ESP32TemperatureHumidity,
    Status,
    ImageData,
    TimelapseConf,
    ThermostatConf,
    ApiKey,
    BMPData,
    CarHeaterStatus,
)
import pytz
import sqlite3
import secrets

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, db_path: str = os.getenv("DB_PATH", os.path.join(tempfile.gettempdir(), "timelapse.db"))):
        from . import DatabaseManager
        self.db = DatabaseManager(db_path)
        self.finland_tz = pytz.timezone('Europe/Helsinki')

    # --- User operations ---
    def register_user(
        self,
        username: str,
        password: str | None = None,
        password_hash: str | None = None,
        is_admin: bool = False,
        is_root_admin: bool = False,
        is_temporary: bool = False,
        expires_at: str | None = None
    ) -> User:
        """
        Creates the user if it doesn't exist, or returns the existing one.
        Uses INSERT OR IGNORE to avoid UNIQUE errors, then SELECT to fetch.
        """
        logger.debug(f"Registering user: {username}")

        # 1) Hash the password up front
        if password_hash is None and password:
            pw_hash = generate_password_hash(password)
        elif password is None and password_hash is None:
            raise ValueError(
                "Either password or password_hash must be provided")
        else:
            pw_hash = password_hash

        # 2) Try to insert; if username exists, this is a no-op
        cursor = self.db.execute_query(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin, is_root_admin, is_temporary, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, pw_hash, 1 if is_admin else 0,
             1 if is_root_admin else 0, 1 if is_temporary else 0, expires_at)
        )
        if cursor.rowcount == 1:
            logger.debug(f"User '{username}' created successfully.")
        else:
            logger.debug(f"User '{username}' already exists, skipping INSERT.")

        # 3) Fetch whatever is now in the table
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,)
        )
        if row is None:
            # This really should never happen
            logger.exception(
                f"After INSERT OR IGNORE, no row for '{username}' found.")
            raise RuntimeError(f"Failed to retrieve user '{username}'")

        logger.debug(f"Returning user '{username}' with id={row['id']}")
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'],
            is_admin=row['is_admin'],
            is_root_admin=row['is_root_admin'],
            is_temporary=row['is_temporary'],
            expires_at=row['expires_at']
        )

    def create_temporary_user(self, username: str, password: str, duration_value: int = 1, duration_unit: str = "hours") -> User:
        """Creates a temporary user with expiration."""
        now = datetime.now(self.finland_tz)
        if duration_unit == "minutes":
            expires = now + timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            expires = now + timedelta(hours=duration_value)
        elif duration_unit == "days":
            expires = now + timedelta(days=duration_value)
        else:
            expires = now + timedelta(hours=duration_value)
        expires_at = expires.isoformat()
        return self.register_user(username, password=password, is_temporary=True, expires_at=expires_at)

    def set_user_as_admin(self, username: str, is_admin: bool) -> None:
        """Sets the is_admin flag for the user with the given username."""
        self.db.execute_query(
            "UPDATE users SET is_admin = ? WHERE username = ?",
            (is_admin, username)
        )

    def authenticate_user(self, username: str, password: str) -> bool:
        row = self.db.fetchone(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        )
        if row is None:
            return False
        return check_password_hash(row['password_hash'], password)

    def get_all_users(self, exclude_admin: bool = False, exclude_current: bool = False, exclude_expired: bool = False) -> list[User]:
        """Palauttaa listan kaikista käyttäjistä."""
        rows = self.db.fetchall(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users",
            ()
        )
        users = [
            User(id=row['id'], username=row['username'],
                 password_hash=row['password_hash'], is_admin=row['is_admin'], is_root_admin=row['is_root_admin'], is_temporary=row['is_temporary'], expires_at=row['expires_at'])
            for row in rows
        ]
        if exclude_admin:
            users = [user for user in users if not user.is_admin]
        if exclude_current:
            users = [user for user in users if user.username !=
                     current_user.get_id()]
        if exclude_expired:
            now = datetime.now(self.finland_tz)
            users = [user for user in users if not user.is_temporary or not user.expires_at or datetime.fromisoformat(
                user.expires_at) > now]
        return users

    def get_user_by_username(self, username: str, include_pw: bool = True) -> User | None:
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_root_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,)
        )
        if not row:
            return None
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'] if include_pw else None,
            is_admin=row['is_admin'],
            is_root_admin=row['is_root_admin'],
            is_temporary=row['is_temporary'],
            expires_at=row['expires_at']
        )

    def delete_user(self, username: str) -> None:
        """Poistaa käyttäjän annetulla käyttäjätunnuksella."""
        self.db.execute_query(
            "DELETE FROM users WHERE username = ?",
            (username,)
        )

    def delete_temporary_users(self) -> None:
        """Deletes all temporary users."""
        self.db.execute_query(
            "DELETE FROM users WHERE is_temporary = 1",
            ()
        )

    def delete_expired_temporary_users(self) -> None:
        """Deletes all expired temporary users."""
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "DELETE FROM users WHERE is_temporary = 1 AND expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )

    def update_user(
        self,
        current_username: str,
        new_username: str | None = None,
        password: str | None = None,
        is_temporary: bool | None = None,
        is_admin: bool | None = None,
        expires_at: str | None = None,
    ) -> User:
        """
        Updates the given user's fields. Pass None for fields you don't want to change.
        If is_temporary is False, expires_at will be set to NULL regardless of value.
        Returns the updated User object.
        """
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (current_username,)
        )
        if row is None:
            raise ValueError("User not found")

        updates: list[str] = []
        params: list[object] = []

        if new_username and new_username != current_username:
            updates.append("username = ?")
            params.append(new_username)

        if password:
            pw_hash = generate_password_hash(password)
            updates.append("password_hash = ?")
            params.append(pw_hash)

        if is_admin is not None:
            updates.append("is_admin = ?")
            params.append(1 if is_admin else 0)

        if is_temporary is not None:
            updates.append("is_temporary = ?")
            params.append(1 if is_temporary else 0)
            if is_temporary:
                # Set provided expires_at (can be None, meaning no expiry yet)
                updates.append("expires_at = ?")
                params.append(expires_at)
            else:
                # Clear expiry when switching to permanent
                updates.append("expires_at = NULL")

        elif expires_at is not None:
            # Only update expiry if caller asked and did not change is_temporary
            updates.append("expires_at = ?")
            params.append(expires_at)

        if not updates:
            # Nothing to change; return current user state
            return User(
                id=row['id'],
                username=row['username'],
                password_hash=row['password_hash'],
                is_admin=row['is_admin'],
                is_temporary=row['is_temporary'],
                expires_at=row['expires_at']
            )

        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ?"
        params.append(current_username)

        try:
            self.db.execute_query(query, tuple(params))
        except sqlite3.IntegrityError as ie:
            # Likely UNIQUE constraint failure on username
            raise ValueError("Käyttäjätunnus on jo käytössä.") from ie

        return self.get_user_by_username(new_username or current_username)

    # --- Sensor data operations ---

    def record_esp32_temphum(self, location: str, temperature: float, humidity: float, ac_on: bool | None = None) -> ESP32TemperatureHumidity:
        now = datetime.now(self.finland_tz).isoformat()
        # Insert with optional AC state flag (nullable)
        self.db.execute_query(
            "INSERT INTO esp32_temphum (location, timestamp, temperature, humidity, ac_on) VALUES (?, ?, ?, ?, ?)",
            (location, now, temperature, humidity,
             None if ac_on is None else (1 if ac_on else 0))
        )
        row = self.db.fetchone(
            "SELECT id, location, timestamp, temperature, humidity, ac_on FROM esp32_temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError(
                "Failed to retrieve inserted esp32_temphum record")
        return ESP32TemperatureHumidity(
            id=row['id'], location=row['location'], timestamp=row['timestamp'],
            temperature=row['temperature'], humidity=row['humidity'],
            ac_on=(None if row['ac_on'] is None else bool(row['ac_on']))
        )

    # --- AC event logging / queries ---
    def record_ac_event(self, is_on: bool, source: str | None = None, note: str | None = None, when_iso: str | None = None) -> None:
        """Insert an AC on/off event.

        :param is_on: True for ON, False for OFF
        :param source: optional tag (e.g., 'thermostat', 'manual')
        :param note: optional message
        :param when_iso: ISO timestamp; if None, uses local now
        """
        ts = when_iso or datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO ac_events (timestamp, is_on, source, note) VALUES (?, ?, ?, ?)",
            (ts, 1 if is_on else 0, source, note)
        )

    def get_ac_events_between(self, start_iso: str, end_iso: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT id, timestamp, is_on, source, note FROM ac_events WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_iso, end_iso)
        )
        return [
            {
                'id': row['id'],
                'timestamp': row['timestamp'],
                'is_on': bool(row['is_on']),
                'source': row['source'],
                'note': row['note']
            }
            for row in rows
        ]

    def get_last_ac_state_before(self, ts_iso: str) -> bool | None:
        row = self.db.fetchone(
            "SELECT is_on FROM ac_events WHERE timestamp <= ? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (ts_iso,)
        )
        if row is None:
            return None
        return bool(row['is_on'])

    def get_last_esp32_temphum(self) -> Optional[ESP32TemperatureHumidity]:
        row = self.db.fetchone(
            "SELECT id, location, timestamp, temperature, humidity, ac_on FROM esp32_temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return ESP32TemperatureHumidity(
            id=row['id'], location=row['location'], timestamp=row['timestamp'],
            temperature=row['temperature'], humidity=row['humidity'],
            ac_on=(None if row['ac_on'] is None else bool(row['ac_on']))
        )

    def get_esp32_temphum_for_date(self, date_str: str, location: str) -> List[ESP32TemperatureHumidity]:
        rows = self.db.fetchall(
            """
            SELECT id, location, timestamp, temperature, humidity, ac_on
              FROM esp32_temphum
             WHERE date(timestamp) = ? AND location = ?
             ORDER BY timestamp
            """,
            (date_str, location)
        )
        return [
            ESP32TemperatureHumidity(
                id=row['id'],
                location=row['location'],
                timestamp=row['timestamp'],
                temperature=row['temperature'],
                humidity=row['humidity'],
                ac_on=(None if row['ac_on'] is None else bool(row['ac_on']))
            )
            for row in rows
        ]

    def get_last_esp32_temphum_for_location(self, location: str) -> Optional[ESP32TemperatureHumidity]:
        """Return the most recent ESP32TemperatureHumidity row for a given location, or None."""
        row = self.db.fetchone(
            """
            SELECT id, location, timestamp, temperature, humidity, ac_on
              FROM esp32_temphum
             WHERE location = ?
             ORDER BY timestamp DESC, id DESC
             LIMIT 1
            """,
            (location,)
        )
        if row is None:
            return None
        return ESP32TemperatureHumidity(
            id=row['id'],
            location=row['location'],
            timestamp=row['timestamp'],
            temperature=row['temperature'],
            humidity=row['humidity'],
            ac_on=(None if row['ac_on'] is None else bool(row['ac_on']))
        )

    def get_unique_locations(self) -> List[Dict[str, Any]]:
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
                "temperature": float(row["temperature"]) if row["temperature"] is not None else None,
                "humidity": float(row["humidity"]) if row["humidity"] is not None else None,
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

    def update_status(self, status: str) -> Status:
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

    def get_last_status(self) -> Optional[Status]:
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

    def log_message(self, message: str, log_type: str = 'info') -> None:
        """
        Logs a message with the given type ('info', 'warning', 'error', 'auth', 'ac').
        """
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO logs (timestamp, type, message) VALUES (?, ?, ?)",
            (now, log_type, message)
        )

    def get_logs(self, limit: int = 100) -> List[dict]:
        """
        Retrieves the most recent log messages.
        """
        rows = self.db.fetchall(
            "SELECT timestamp, type, message FROM logs ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in rows]

    # --- API key management ---
    def create_api_key(self, name: str, created_by: str | None = None) -> tuple[ApiKey, str]:
        """Create a new API key. Stores only a salted hash; returns the full token once.

        Token format: 'sk_' + key_id + '_' + secret
        - key_id: 16 hex chars (64-bit randomness)
        - secret: 43+ chars URL-safe random string
        """
        if not name or not name.strip():
            raise ValueError("Key name is required")
        key_id = secrets.token_hex(8)  # 64-bit id, hex
        secret = secrets.token_urlsafe(32)
        token = f"sk_{key_id}_{secret}"
        # Use a password hash to store the secret (includes salt and iterations)
        secret_hash = generate_password_hash(secret)
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO api_keys (key_id, name, secret_hash, created_at, created_by, revoked, last_used_at) VALUES (?, ?, ?, ?, ?, 0, NULL)",
            (key_id, name.strip(), secret_hash, now, created_by)
        )
        row = self.db.fetchone(
            "SELECT id, key_id, name, created_at, created_by, revoked, last_used_at FROM api_keys WHERE key_id = ?",
            (key_id,)
        )
        if row is None:
            raise RuntimeError("Failed to create API key")
        api_key = ApiKey(
            id=row['id'], key_id=row['key_id'], name=row['name'], created_at=row['created_at'],
            created_by=row['created_by'], revoked=bool(row['revoked']), last_used_at=row['last_used_at']
        )
        return api_key, token

    def list_api_keys(self) -> list[ApiKey]:
        rows = self.db.fetchall(
            "SELECT id, key_id, name, created_at, created_by, revoked, last_used_at FROM api_keys ORDER BY id DESC",
            ()
        )
        return [
            ApiKey(
                id=row['id'], key_id=row['key_id'], name=row['name'], created_at=row['created_at'],
                created_by=row['created_by'], revoked=bool(row['revoked']), last_used_at=row['last_used_at']
            )
            for row in rows
        ]

    def delete_api_key(self, key_id: str) -> None:
        self.db.execute_query(
            "DELETE FROM api_keys WHERE key_id = ?", (key_id,))

    def revoke_api_key(self, key_id: str) -> None:
        self.db.execute_query(
            "UPDATE api_keys SET revoked = 1 WHERE key_id = ?", (key_id,))

    def verify_api_key_token(self, token: str) -> dict | None:
        """Verify a presented API key token and return key metadata on success.

        On success, updates last_used_at. Returns dict with key fields; otherwise None.
        """
        try:
            if not token or not token.startswith('sk_'):
                return None
            rest = token[3:]
            idx = rest.find('_')
            if idx <= 0:
                return None
            key_id = rest[:idx]
            secret = rest[idx + 1:]
            if not key_id or not secret:
                return None
        except Exception:
            return None

        row = self.db.fetchone(
            "SELECT id, key_id, name, secret_hash, created_at, created_by, revoked, last_used_at FROM api_keys WHERE key_id = ?",
            (key_id,)
        )
        if row is None or bool(row['revoked']):
            return None
        if not check_password_hash(row['secret_hash'], secret):
            return None
        # Update last_used_at best-effort
        try:
            now = datetime.now(self.finland_tz).isoformat()
            self.db.execute_query(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row['id']))
        except Exception:
            pass
        return {
            'id': row['id'],
            'key_id': row['key_id'],
            'name': row['name'],
            'created_at': row['created_at'],
            'created_by': row['created_by'],
            'revoked': bool(row['revoked']),
            'last_used_at': row['last_used_at'],
        }

    # --- Thermostat configuration operations ---
    def get_thermostat_conf(self) -> ThermostatConf | None:
        row = self.db.fetchone(
            """
            SELECT id, sleep_active, sleep_start, sleep_stop, sleep_weekly, control_locations, target_temp, pos_hysteresis, neg_hysteresis, thermo_active,
                   total_on_s, total_off_s,
                   min_on_s, min_off_s, poll_interval_s, smooth_window, max_stale_s,
                   current_phase, phase_started_at
              FROM thermostat_conf
             WHERE id = 1
            """
        )
        if row is None:
            return None
        return ThermostatConf(
            id=row['id'],
            sleep_active=bool(row['sleep_active']),
            sleep_start=row['sleep_start'],
            sleep_stop=row['sleep_stop'],
            sleep_weekly=(row['sleep_weekly']
                          if 'sleep_weekly' in row.keys() else None),
            control_locations=(
                row['control_locations'] if 'control_locations' in row.keys() else None),
            target_temp=float(row['target_temp']),
            pos_hysteresis=float(row['pos_hysteresis']),
            neg_hysteresis=float(row['neg_hysteresis']),
            thermo_active=bool(row['thermo_active']
                               ) if 'thermo_active' in row.keys() else True,
            min_on_s=int(row['min_on_s']) if 'min_on_s' in row.keys() else 240,
            min_off_s=int(row['min_off_s']
                          ) if 'min_off_s' in row.keys() else 240,
            poll_interval_s=int(
                row['poll_interval_s']) if 'poll_interval_s' in row.keys() else 15,
            smooth_window=int(row['smooth_window']
                              ) if 'smooth_window' in row.keys() else 5,
            max_stale_s=int(row['max_stale_s']) if 'max_stale_s' in row.keys(
            ) and row['max_stale_s'] is not None else 120,
            current_phase=row['current_phase'] if 'current_phase' in row.keys(
            ) else None,
            phase_started_at=row['phase_started_at'] if 'phase_started_at' in row.keys(
            ) else None,
        )

    def save_thermostat_conf(
        self,
        *,
        sleep_active: bool,
        sleep_start: str | None,
        sleep_stop: str | None,
        sleep_weekly: str | None = None,
        control_locations: str | None = None,
        target_temp: float,
        pos_hysteresis: float,
        neg_hysteresis: float,
        thermo_active: bool,
        # historical totals no longer used; kept for backward compat at DB level
        total_on_s: int = 0,
        total_off_s: int = 0,
        min_on_s: int = 240,
        min_off_s: int = 240,
        poll_interval_s: int = 15,
        smooth_window: int = 5,
        max_stale_s: int | None = 120,
        current_phase: str | None = None,
        phase_started_at: str | None = None,
    ) -> ThermostatConf:
        self.db.execute_query(
            """
            INSERT INTO thermostat_conf (id, sleep_active, sleep_start, sleep_stop, sleep_weekly, control_locations, target_temp, pos_hysteresis, neg_hysteresis, thermo_active,
                                         total_on_s, total_off_s, min_on_s, min_off_s, poll_interval_s, smooth_window, max_stale_s,
                                         current_phase, phase_started_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sleep_active = excluded.sleep_active,
                sleep_start = excluded.sleep_start,
                sleep_stop = excluded.sleep_stop,
                sleep_weekly = excluded.sleep_weekly,
                control_locations = excluded.control_locations,
                target_temp = excluded.target_temp,
                pos_hysteresis = excluded.pos_hysteresis,
                neg_hysteresis = excluded.neg_hysteresis,
                thermo_active = excluded.thermo_active,
                total_on_s = excluded.total_on_s,
                total_off_s = excluded.total_off_s,
                min_on_s = excluded.min_on_s,
                min_off_s = excluded.min_off_s,
                poll_interval_s = excluded.poll_interval_s,
                smooth_window = excluded.smooth_window,
                max_stale_s = excluded.max_stale_s,
                current_phase = excluded.current_phase,
                phase_started_at = excluded.phase_started_at
            """,
            (
                1 if sleep_active else 0,
                sleep_start,
                sleep_stop,
                sleep_weekly,
                control_locations,
                float(target_temp),
                float(pos_hysteresis),
                float(neg_hysteresis),
                1 if thermo_active else 0,
                int(total_on_s),
                int(total_off_s),
                int(min_on_s),
                int(min_off_s),
                int(poll_interval_s),
                int(smooth_window),
                None if max_stale_s is None else int(max_stale_s),
                current_phase,
                phase_started_at,
            ),
        )
        conf = self.get_thermostat_conf()
        if conf is None:
            # This should never happen after UPSERT
            raise RuntimeError("Failed to save thermostat configuration")
        return conf

    def ensure_thermostat_conf_seeded_from(self, cfg: object | None = None) -> ThermostatConf:
        """
        Seed the thermostat configuration row from a given config-like object
        that provides attributes: setpoint_c, pos_hysteresis, neg_hysteresis, sleep_enabled,
        sleep_start, sleep_stop. If a row already exists, it is returned as-is.
        """
        existing = self.get_thermostat_conf()
        if existing is not None:
            return existing
        # Extract with safe fallbacks (support legacy names too)

        def _getattr(name: str, default):
            if cfg is None:
                return default
            return getattr(cfg, name, default)
        target_temp = float(
            _getattr('target_temp', _getattr('setpoint_c', 24.5)))
        pos_h = float(_getattr('pos_hysteresis', 0.5))
        neg_h = float(_getattr('neg_hysteresis', 0.5))
        sleep_active = bool(
            _getattr('sleep_active', _getattr('sleep_enabled', True)))
        thermo_active = bool(_getattr('thermo_active', True))
        sleep_start = _getattr('sleep_start', None)
        sleep_stop = _getattr('sleep_stop', None)
        total_on_s = int(_getattr('total_on_s', 0) or 0)
        total_off_s = int(_getattr('total_off_s', 0) or 0)
        min_on_s = int(_getattr('min_on_s', 240))
        min_off_s = int(_getattr('min_off_s', 240))
        poll_interval_s = int(_getattr('poll_interval_s', 15))
        smooth_window = int(_getattr('smooth_window', 5))
        max_stale_s = _getattr('max_stale_s', 120)
        try:
            max_stale_s = None if max_stale_s is None else int(max_stale_s)
        except Exception:
            max_stale_s = 120
        return self.save_thermostat_conf(
            sleep_active=sleep_active,
            sleep_start=sleep_start,
            sleep_stop=sleep_stop,
            total_on_s=total_on_s,
            total_off_s=total_off_s,
            target_temp=target_temp,
            pos_hysteresis=pos_h,
            neg_hysteresis=neg_h,
            thermo_active=thermo_active,
            min_on_s=min_on_s,
            min_off_s=min_off_s,
            poll_interval_s=poll_interval_s,
            smooth_window=smooth_window,
            max_stale_s=max_stale_s,
            current_phase=_getattr('current_phase', 'off'),
            phase_started_at=_getattr('phase_started_at', None),
        )

    def record_bmp_sensor_data(self, temperature: float, pressure: float, altitude: float) -> BMPData:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO bmp_sensor_data (timestamp, temperature, pressure, altitude) VALUES (?, ?, ?, ?)",
            (now, temperature, pressure, altitude)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, pressure, altitude FROM bmp_sensor_data ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError(
                "Failed to retrieve inserted bmp_sensor_data record")
        return BMPData(
            id=row['id'],
            timestamp=row['timestamp'],
            temperature=row['temperature'],
            pressure=row['pressure'],
            altitude=row['altitude']
        )

    def get_last_bmp_sensor_data(self) -> Optional[BMPData]:
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, pressure, altitude FROM bmp_sensor_data ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return BMPData(
            id=row['id'],
            timestamp=row['timestamp'],
            temperature=row['temperature'],
            pressure=row['pressure'],
            altitude=row['altitude']
        )

    def get_bmp_sensor_data_for_date(self, date_str: str) -> List[BMPData]:
        rows = self.db.fetchall(
            """
            SELECT id, timestamp, temperature, pressure, altitude
              FROM bmp_sensor_data
             WHERE date(timestamp) = ?
             ORDER BY timestamp
            """,
            (date_str,)
        )
        return [
            BMPData(
                id=row['id'],
                timestamp=row['timestamp'],
                temperature=row['temperature'],
                pressure=row['pressure'],
                altitude=row['altitude']
            )
            for row in rows
        ]

    # --- Car Heater operations ---
    # --- Car Heater operations ---

    def record_car_heater_status(self, status: CarHeaterStatus) -> CarHeaterStatus:
        """
        Insert a new car heater status row and return it with id set.
        Uses the timestamp provided in the CarHeaterStatus.
        """
        self.db.execute_query(
            """
            INSERT INTO car_heater_status (
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status.timestamp,
                1 if status.is_heater_on else 0,
                float(status.instant_power_w),
                status.voltage_v,
                status.current_a,
                status.energy_total_wh,
                status.energy_last_min_wh,
                status.energy_ts,
                status.device_temp_c,
                status.device_temp_f,
                status.ambient_temp,
                status.source,
            ),
        )

        # Fetch the just-inserted row (same pattern as other record_* helpers)
        row = self.db.fetchone(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY id DESC
            LIMIT 1
            """
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted car_heater_status record")

        return CarHeaterStatus(
            id=row["id"],
            timestamp=row["timestamp"],
            is_heater_on=bool(row["is_heater_on"]),
            instant_power_w=row["instant_power_w"],
            voltage_v=row["voltage_v"],
            current_a=row["current_a"],
            energy_total_wh=row["energy_total_wh"],
            energy_last_min_wh=row["energy_last_min_wh"],
            energy_ts=row["energy_ts"],
            device_temp_c=row["device_temp_c"],
            device_temp_f=row["device_temp_f"],
            ambient_temp=row["ambient_temp"],
            source=row["source"],
        )

    def get_last_car_heater_status(self) -> CarHeaterStatus | None:
        """
        Return the most recent car heater status row, or None if table is empty.
        """
        row = self.db.fetchone(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        )
        if row is None:
            return None

        return CarHeaterStatus(
            id=row["id"],
            timestamp=row["timestamp"],
            is_heater_on=bool(row["is_heater_on"]),
            instant_power_w=row["instant_power_w"],
            voltage_v=row["voltage_v"],
            current_a=row["current_a"],
            energy_total_wh=row["energy_total_wh"],
            energy_last_min_wh=row["energy_last_min_wh"],
            energy_ts=row["energy_ts"],
            device_temp_c=row["device_temp_c"],
            device_temp_f=row["device_temp_f"],
            ambient_temp=row["ambient_temp"],
            source=row["source"],
        )

    def get_car_heater_status_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> list[CarHeaterStatus]:
        """
        Get all car heater status rows between two ISO timestamps (inclusive),
        ordered by timestamp.
        """
        rows = self.db.fetchall(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp, id
            """,
            (start_iso, end_iso),
        )

        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]

    def get_recent_car_heater_status(
        self,
        limit: int = 200,
    ) -> list[CarHeaterStatus]:
        """
        Get the latest N car heater status rows, newest first.
        Handy for graphs.
        """
        rows = self.db.fetchall(
            f"""
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]

    def get_car_heater_status_for_date(
        self,
        date_str: str,
    ) -> list[CarHeaterStatus]:
        """
        Return all car heater status records for a given local date.
        """
        rows = self.db.fetchall(
            """
            SELECT
                id,
                timestamp,
                is_heater_on,
                instant_power_w,
                voltage_v,
                current_a,
                energy_total_wh,
                energy_last_min_wh,
                energy_ts,
                device_temp_c,
                device_temp_f,
                ambient_temp,
                source
            FROM car_heater_status
            WHERE date(timestamp) = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (date_str,)
        )
        return [
            CarHeaterStatus(
                id=row["id"],
                timestamp=row["timestamp"],
                is_heater_on=bool(row["is_heater_on"]),
                instant_power_w=row["instant_power_w"],
                voltage_v=row["voltage_v"],
                current_a=row["current_a"],
                energy_total_wh=row["energy_total_wh"],
                energy_last_min_wh=row["energy_last_min_wh"],
                energy_ts=row["energy_ts"],
                device_temp_c=row["device_temp_c"],
                device_temp_f=row["device_temp_f"],
                ambient_temp=row["ambient_temp"],
                source=row["source"],
            )
            for row in rows
        ]
