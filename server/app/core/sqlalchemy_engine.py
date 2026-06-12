"""SQLAlchemy engine utilities (phase-in for Alembic)."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool, QueuePool

logger = logging.getLogger(__name__)

_ENGINES: dict[str, Engine] = {}


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Enable SQLite foreign key enforcement."""
    logger.debug("_enable_sqlite_foreign_keys called")
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        logger.debug("Failed to enable SQLite foreign keys", exc_info=True)


def _redact_db_url(db_url: str) -> str:
    """Redact password from DB URL for logging."""
    if not db_url:
        return db_url
    try:
        if "@" in db_url and "://" in db_url:
            prefix, rest = db_url.split("://", 1)
            if "@" in rest and ":" in rest.split("@", 1)[0]:
                userinfo, host = rest.split("@", 1)
                user = userinfo.split(":", 1)[0]
                return f"{prefix}://{user}:***@{host}"
    except Exception:
        logger.debug("_redact_db_url failed", exc_info=True)
    return db_url


def get_engine_for_url(db_url: str) -> Engine:
    """Return a singleton SQLAlchemy engine for the given DB URL."""
    logger.debug("get_engine_for_url called url=%s", _redact_db_url(db_url))
    if not db_url:
        raise ValueError("db_url is required for SQLAlchemy engine")

    if db_url in _ENGINES:
        return _ENGINES[db_url]

    is_sqlite = db_url.startswith("sqlite")
    connect_args = {}
    engine_options = {
        "connect_args": connect_args,
        "future": True,
    }
    if is_sqlite:
        connect_args = {"check_same_thread": False}
        engine_options["connect_args"] = connect_args
        engine_options["poolclass"] = NullPool
        logger.debug("Creating SQLite engine with NullPool")
    else:
        engine_options.update(
            {
                "poolclass": QueuePool,
                "pool_size": 5,
                "max_overflow": 5,
                "pool_timeout": 30,
                "pool_pre_ping": True,
                "pool_use_lifo": True,
            }
        )
        logger.debug(
            "Creating pooled SQLAlchemy engine pool_size=%s max_overflow=%s",
            engine_options["pool_size"],
            engine_options["max_overflow"],
        )

    engine = create_engine(db_url, **engine_options)
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)

    _ENGINES[db_url] = engine
    return engine


def get_engine(db_path: str) -> Engine:
    """Return a singleton SQLAlchemy engine for the given SQLite DB path."""
    logger.debug("get_engine called db_path=%s", db_path)
    if not db_path:
        raise ValueError("db_path is required for SQLAlchemy engine")
    db_url = f"sqlite:///{db_path}"
    return get_engine_for_url(db_url)


def dispose_engine() -> None:
    """Dispose the SQLAlchemy engine (useful for tests or shutdown)."""
    logger.debug("dispose_engine called")
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()
