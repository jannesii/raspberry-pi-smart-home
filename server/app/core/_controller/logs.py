import logging
from datetime import datetime

from sqlalchemy import Engine, desc, func, select, text

from ..schema import logs as logs_table

logger = logging.getLogger(__name__)


class LogsMixin:
    def log_message(self, message: str, log_type: str = "info", timestamp=None) -> None:
        """
        Logs a message with the given type ('info', 'warning', 'error', 'auth', 'ac', 'car_heater', 'kfactor).
        Also emits the log to Socket.IO for real-time updates.
        """
        logger.debug("log_message called log_type=%s message_len=%s", log_type, len(message or ""))
        now = timestamp or datetime.now(self.finland_tz).isoformat()
        use_sa: bool = self._use_sa
        sa_engine: Engine | None = self._sa_engine

        logger.debug(
            "log_message write_mode use_sqlalchemy=%s sa_engine=%s use_sa_writes=%s",
            use_sa,
            "set" if sa_engine else "none",
            use_sa,
        )
        try:
            try:
                url = sa_engine.url.render_as_string(hide_password=True)
            except Exception:
                url = "unknown"
            logger.debug(
                "log_message sqlA insert sa_engine_url=%s dialect=%s",
                url,
                sa_engine.dialect.name,
            )
            stmt = logs_table.insert().values(timestamp=now, type=log_type, message=message)
            if sa_engine.dialect.name == "postgresql":
                stmt = stmt.returning(logs_table.c.id)
            with sa_engine.begin() as conn:
                result = conn.execute(stmt)
                sa_id = None
                if result.returns_rows:
                    row = result.fetchone()
                    sa_id = row[0] if row else None
                else:
                    try:
                        pk = result.inserted_primary_key
                        sa_id = pk[0] if pk else None
                    except Exception:
                        sa_id = None
            logger.debug("log_message sqlA_insert_id=%s", sa_id)
        except Exception:
            logger.exception("log_message sqlA insert failed")

        # Emit to Socket.IO for real-time log viewers
        self._emit_db_log(
            {
                "id": sa_id,
                "timestamp": now,
                "type": log_type,
                "message": message,
            }
        )
        return

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
        use_sa: bool = False  # self._use_sa
        sa_engine: Engine | None = self._sa_engine
        logger.debug(
            "get_logs read_mode use_sqlalchemy=%s sa_engine=%s",
            use_sa,
            "set" if sa_engine else "none",
        )

        try:
            url = sa_engine.url.render_as_string(hide_password=True)
        except Exception:
            url = "unknown"
        logger.debug("get_logs sa_engine_url=%s dialect=%s", url, sa_engine.dialect.name)
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
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        if rows:
            logger.debug(
                "get_logs sqlA rows=%s first_id=%s last_id=%s",
                len(rows),
                rows[0].get("id"),
                rows[-1].get("id"),
            )
        else:
            logger.debug("get_logs sqlA rows=0")
        logger.debug("get_logs retrieved %s rows", len(rows))
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
        use_sa: bool = self._use_sa
        sa_engine: Engine | None = self._sa_engine
        logger.debug(
            "get_logs_filtered read_mode use_sqlalchemy=%s sa_engine=%s",
            use_sa,
            "set" if sa_engine else "none",
        )
        try:
            url = sa_engine.url.render_as_string(hide_password=True)
        except Exception:
            url = "unknown"
        logger.debug(
            "get_logs_filtered sa_engine_url=%s dialect=%s",
            url,
            sa_engine.dialect.name,
        )
        stmt = select(
            logs_table.c.id,
            logs_table.c.timestamp,
            logs_table.c.type,
            logs_table.c.message,
        )
        if log_type:
            stmt = stmt.where(logs_table.c.type == log_type)
        if search:
            dialect = getattr(sa_engine.dialect, "name", "unknown")
            logger.debug("get_logs_filtered using dialect=%s for search", dialect)
            if dialect == "sqlite":
                stmt = stmt.where(logs_table.c.message.like(f"%{search}%"))
            else:
                stmt = stmt.where(logs_table.c.message.ilike(f"%{search}%"))
        if before_id is not None:
            stmt = stmt.where(logs_table.c.id < before_id)
        stmt = stmt.order_by(desc(logs_table.c.timestamp)).limit(limit + 1)
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        logs = [dict(row) for row in rows]
        has_more = len(logs) > limit
        if has_more:
            logs = logs[:limit]
        if logs:
            logger.debug(
                "get_logs_filtered sqlA logs=%s has_more=%s first_id=%s last_id=%s",
                len(logs),
                has_more,
                logs[0].get("id"),
                logs[-1].get("id"),
            )
        else:
            logger.debug("get_logs_filtered sqlA logs=0 has_more=%s", has_more)
        return logs, has_more

    def get_log_types(self) -> list[str]:
        """Get all distinct log types in the database."""
        logger.debug("get_log_types called")
        use_sa: bool = self._use_sa
        sa_engine: Engine | None = self._sa_engine
        logger.debug(
            "get_log_types read_mode use_sqlalchemy=%s sa_engine=%s",
            use_sa,
            "set" if sa_engine else "none",
        )
        stmt = select(logs_table.c.type).distinct().order_by(logs_table.c.type)
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).all()
        logger.debug("get_log_types sqlA count=%s", len(rows))
        return [row[0] for row in rows]

    def get_logs_count(self, log_type: str | None = None) -> int:
        """Get total count of logs, optionally filtered by type."""
        logger.debug("get_logs_count called log_type=%s", log_type)
        use_sa: bool = self._use_sa
        sa_engine: Engine | None = self._sa_engine
        logger.debug(
            "get_logs_count read_mode use_sqlalchemy=%s sa_engine=%s",
            use_sa,
            "set" if sa_engine else "none",
        )
        stmt = select(func.count()).select_from(logs_table)
        if log_type:
            stmt = stmt.where(logs_table.c.type == log_type)
        with sa_engine.connect() as conn:
            count = conn.execute(stmt).scalar()
        logger.debug("get_logs_count sqlA count=%s", count)
        return int(count or 0)

    def delete_duplicate_logs_postgres(self) -> int:
        """Delete duplicate log rows in Postgres (keeps the smallest id per timestamp/type/message)."""
        logger.debug("delete_duplicate_logs_postgres called")
        use_sa: bool = self._use_sa
        sa_engine: Engine | None = self._sa_engine
        logger.debug(
            "delete_duplicate_logs_postgres read_mode use_sqlalchemy=%s sa_engine=%s",
            use_sa,
            "set" if sa_engine else "none",
        )
        if not use_sa or sa_engine is None:
            logger.debug("delete_duplicate_logs_postgres skipped (no SQLAlchemy engine)")
            return 0
        if sa_engine.dialect.name != "postgresql":
            logger.debug(
                "delete_duplicate_logs_postgres skipped (dialect=%s)", sa_engine.dialect.name
            )
            return 0

        try:
            with sa_engine.begin() as conn:
                before = conn.execute(select(func.count()).select_from(logs_table)).scalar()
                logger.debug("delete_duplicate_logs_postgres before_count=%s", before)
                result = conn.execute(
                    text(
                        """
                        WITH ranked AS (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY timestamp, type, message
                                       ORDER BY id
                                   ) AS rn
                            FROM logs
                        )
                        DELETE FROM logs
                        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                        """
                    )
                )
                deleted = int(result.rowcount or 0)
                after = conn.execute(select(func.count()).select_from(logs_table)).scalar()
                logger.debug(
                    "delete_duplicate_logs_postgres deleted=%s after_count=%s",
                    deleted,
                    after,
                )
                return deleted
        except Exception:
            logger.exception("delete_duplicate_logs_postgres failed")
            return 0

    def delete_duplicate_logs_sqlite(self) -> int:
        """Delete duplicate log rows in SQLite (keeps the smallest id per timestamp/type/message)."""
        logger.debug("delete_duplicate_logs_sqlite called")
        try:
            before_row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs")
            before = int(before_row["cnt"] if before_row else 0)
            logger.debug("delete_duplicate_logs_sqlite before_count=%s", before)
            self.db.execute_query(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY timestamp, type, message
                               ORDER BY id
                           ) AS rn
                    FROM logs
                )
                DELETE FROM logs
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """
            )
            after_row = self.db.fetchone("SELECT COUNT(*) as cnt FROM logs")
            after = int(after_row["cnt"] if after_row else 0)
            deleted = max(0, before - after)
            logger.debug(
                "delete_duplicate_logs_sqlite deleted=%s after_count=%s",
                deleted,
                after,
            )
            return deleted
        except Exception:
            logger.exception("delete_duplicate_logs_sqlite failed")
            return 0
