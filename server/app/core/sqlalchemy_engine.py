"""SQLAlchemy engine utilities (phase-in for Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Enable SQLite foreign key enforcement."""
    logger.debug("_enable_sqlite_foreign_keys called")
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        logger.debug("Failed to enable SQLite foreign keys", exc_info=True)


def get_engine(db_path: str) -> Engine:
    """Return a singleton SQLAlchemy engine for the given DB path."""
    logger.debug("get_engine called db_path=%s", db_path)
    if not db_path:
        raise ValueError("db_path is required for SQLAlchemy engine")

    global _ENGINE
    if _ENGINE is None:
        db_url = f"sqlite:///{db_path}"
        logger.debug("get_engine creating engine url=%s", db_url)
        _ENGINE = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
            future=True,
        )
        event.listen(_ENGINE, "connect", _enable_sqlite_foreign_keys)
    return _ENGINE


def dispose_engine() -> None:
    """Dispose the SQLAlchemy engine (useful for tests or shutdown)."""
    logger.debug("dispose_engine called")
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None
