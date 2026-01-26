import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class LogsMixin:
    def log_message(self, message: str, log_type: str = "info") -> None:
        """
        Logs a message with the given type ('info', 'warning', 'error', 'auth', 'ac', 'car_heater', 'kfactor).
        Also emits the log to Socket.IO for real-time updates.
        """
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO logs (timestamp, type, message) VALUES (?, ?, ?)", (now, log_type, message)
        )
        # Get the inserted log ID for real-time emit
        row = self.db.fetchone("SELECT last_insert_rowid() as id")
        log_id = row["id"] if row else None

        # Emit to Socket.IO for real-time log viewers
        self._emit_db_log(
            {
                "id": log_id,
                "timestamp": now,
                "type": log_type,
                "message": message,
            }
        )

    def _emit_db_log(self, log_entry: dict) -> None:
        """Emit a new log entry to Socket.IO subscribers."""
        try:
            from flask import current_app

            socketio = current_app.extensions.get("socketio")
            if socketio:
                socketio.emit("db_log", log_entry)
        except Exception:
            # Don't fail logging if Socket.IO emit fails
            pass

    def get_logs(self, limit: int = 100) -> list[dict]:
        """
        Retrieves the most recent log messages.
        """
        rows = self.db.fetchall(
            "SELECT id, timestamp, type, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    def get_logs_filtered(
        self,
        log_type: str | None = None,
        search: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> tuple[list[dict], bool]:
        """
        Retrieve filtered log messages with pagination.

        Args:
            log_type: Filter by log type (None = all types)
            search: Case-insensitive search in message
            before_id: Get logs with id < before_id (for pagination)
            limit: Max number of logs to return

        Returns:
            Tuple of (logs list, has_more boolean)
        """
        query = "SELECT id, timestamp, type, message FROM logs WHERE 1=1"
        params: list = []

        if log_type:
            query += " AND type = ?"
            params.append(log_type)

        if search:
            query += " AND message LIKE ?"
            params.append(f"%{search}%")

        if before_id is not None:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit + 1)  # Fetch one extra to check has_more

        rows = self.db.fetchall(query, tuple(params))
        logs = [dict(row) for row in rows]

        has_more = len(logs) > limit
        if has_more:
            logs = logs[:limit]

        return logs, has_more

    def get_log_types(self) -> list[str]:
        """Get all distinct log types in the database."""
        rows = self.db.fetchall("SELECT DISTINCT type FROM logs ORDER BY type")
        return [row["type"] for row in rows]

    def get_logs_count(self, log_type: str | None = None) -> int:
        """Get total count of logs, optionally filtered by type."""
        if log_type:
            row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs WHERE type = ?", (log_type,))
        else:
            row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs")
        return row["cnt"] if row else 0
