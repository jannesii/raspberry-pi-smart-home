from __future__ import annotations

from sqlalchemy.pool import NullPool, QueuePool

from app.core.sqlalchemy_engine import dispose_engine, get_engine_for_url


def setup_function() -> None:
    dispose_engine()


def teardown_function() -> None:
    dispose_engine()


def test_sqlite_engine_keeps_null_pool() -> None:
    engine = get_engine_for_url("sqlite:///:memory:")

    assert isinstance(engine.pool, NullPool)


def test_postgresql_engine_reuses_bounded_connections() -> None:
    engine = get_engine_for_url(
        "postgresql+psycopg://test_user:test_password@localhost/test_database"
    )

    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 5
    assert engine.pool._pre_ping is True
