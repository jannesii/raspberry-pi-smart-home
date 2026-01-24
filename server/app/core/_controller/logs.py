import os
import tempfile
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from flask_login import current_user
from werkzeug.security import generate_password_hash, check_password_hash
import logging

from .. import (
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
from ...services.car_heater.car_heater_models import ChargeModeState, KeepAtTempSettings
import pytz
import sqlite3
import secrets

logger = logging.getLogger(__name__)

class LogsMixin:
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

