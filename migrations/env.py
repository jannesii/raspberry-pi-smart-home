from __future__ import annotations

import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Same file the service loads (run.py, systemd EnvironmentFile), independent of
# the working directory. An exported DATABASE_URL wins, so restore checks can
# target an isolated database explicitly.
load_dotenv(Path(__file__).resolve().parents[1] / "jannenkoti.env", override=False)

# Alembic Config object, provides access to values in alembic.ini
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from app.core.schema import metadata as target_metadata


def _get_db_url() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        logger.debug("_get_db_url resolved DATABASE_URL")
        return db_url
    url = config.get_main_option("sqlalchemy.url")
    logger.debug("_get_db_url using alembic.ini url=%s", url)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _get_db_url()
    logger.debug("run_migrations_offline url=%s", url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    url = _get_db_url()
    logger.debug("run_migrations_online url=%s", url)
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
