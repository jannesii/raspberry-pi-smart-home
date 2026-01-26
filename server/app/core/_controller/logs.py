import logging
from datetime import datetime

from sqlalchemy import desc, func, select

from ..schema import logs as logs_table

logger = logging.getLogger(__name__)


class LogsMixin:
    def log_message(self, message: str, log_type: str = "info") -> None:
        """
        Logs a message with the given type ('info', 'warning', 'error', 'auth', 'ac', 'car_heater', 'kfactor).
        Also emits the log to Socket.IO for real-time updates.
        """
        logger.debug("log_message called log_type=%s message_len=%s", log_type, len(message or ""))
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
        logger.debug("_emit_db_log called keys=%s", list(log_entry.keys()))
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
        logger.debug("get_logs called limit=%s", limit)
        if getattr(self, "_use_sqlalchemy_reads", False) and getattr(self, "_sa_engine", None):
            logger.debug("get_logs using SQLAlchemy reads")
            stmt = (
                select(
                    logs_table.c.id,
                    logs_table.c.timestamp,
                    logs_table.c.type,
                    logs_table.c.message,
                )
                .order_by(desc(logs_table.c.id))
                .limit(limit)
            )
            with self._sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
            return [dict(row) for row in rows]

        logger.debug("get_logs using sqlite3 reads")
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
        logger.debug(
            "get_logs_filtered called log_type=%s search=%s before_id=%s limit=%s",
            log_type,
            search,
            before_id,
            limit,
        )
        if getattr(self, "_use_sqlalchemy_reads", False) and getattr(self, "_sa_engine", None):
            logger.debug("get_logs_filtered using SQLAlchemy reads")
            stmt = select(
                logs_table.c.id,
                logs_table.c.timestamp,
                logs_table.c.type,
                logs_table.c.message,
            )
            if log_type:
                stmt = stmt.where(logs_table.c.type == log_type)
            if search:
                dialect = getattr(self._sa_engine.dialect, "name", "unknown")
                logger.debug("get_logs_filtered using dialect=%s for search", dialect)
                if dialect == "sqlite":
                    stmt = stmt.where(logs_table.c.message.like(f"%{search}%"))
                else:
                    stmt = stmt.where(logs_table.c.message.ilike(f"%{search}%"))
            if before_id is not None:
                stmt = stmt.where(logs_table.c.id < before_id)
            stmt = stmt.order_by(desc(logs_table.c.id)).limit(limit + 1)
            with self._sa_engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
            logs = [dict(row) for row in rows]
            has_more = len(logs) > limit
            if has_more:
                logs = logs[:limit]
            return logs, has_more

        logger.debug("get_logs_filtered using sqlite3 reads")
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
        logger.debug("get_log_types called")
        if getattr(self, "_use_sqlalchemy_reads", False) and getattr(self, "_sa_engine", None):
            logger.debug("get_log_types using SQLAlchemy reads")
            stmt = select(logs_table.c.type).distinct().order_by(logs_table.c.type)
            with self._sa_engine.connect() as conn:
                rows = conn.execute(stmt).all()
            return [row[0] for row in rows]

        logger.debug("get_log_types using sqlite3 reads")
        rows = self.db.fetchall("SELECT DISTINCT type FROM logs ORDER BY type")
        return [row["type"] for row in rows]

    def get_logs_count(self, log_type: str | None = None) -> int:
        """Get total count of logs, optionally filtered by type."""
        logger.debug("get_logs_count called log_type=%s", log_type)
        if getattr(self, "_use_sqlalchemy_reads", False) and getattr(self, "_sa_engine", None):
            logger.debug("get_logs_count using SQLAlchemy reads")
            stmt = select(func.count()).select_from(logs_table)
            if log_type:
                stmt = stmt.where(logs_table.c.type == log_type)
            with self._sa_engine.connect() as conn:
                count = conn.execute(stmt).scalar()
            return int(count or 0)

        logger.debug("get_logs_count using sqlite3 reads")
        if log_type:
            row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs WHERE type = ?", (log_type,))
        else:
            row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs")
        return row["cnt"] if row else 0
