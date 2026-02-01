from __future__ import annotations

import logging

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import CarHeaterReadyByConfig, CarHeaterReadyByState
from ..schema import car_heater_ready_by_config, car_heater_ready_by_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CarHeaterReadyByMixin:
    # --- Ready-by state (single-row settings) ---
    def get_ready_by_state(self) -> CarHeaterReadyByState | None:
        """Get ready-by state (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_ready_by_state called")

        try:
            stmt = select(car_heater_ready_by_state).where(car_heater_ready_by_state.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No ready_by_state record found")
                return None

            return CarHeaterReadyByState(
                id=1,
                state_json=row["state_json"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting ready_by_state: %s", e)
            raise

    def save_ready_by_state(self, state: CarHeaterReadyByState) -> None:
        """Save ready-by state (upsert singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_ready_by_state called")

        try:
            # Use pg_insert with on_conflict_do_update for upsert behavior
            stmt = pg_insert(car_heater_ready_by_state).values(
                id=1,
                state_json=state.state_json,
                updated_ts=state.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "state_json": stmt.excluded.state_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving ready_by_state: %s", e)
            raise

    # --- Ready-by config (single-row settings) ---
    def get_ready_by_config(self) -> CarHeaterReadyByConfig | None:
        """Get ready-by config (singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("get_ready_by_config called")

        try:
            stmt = select(car_heater_ready_by_config).where(car_heater_ready_by_config.c.id == 1)
            with sa_engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()

            if row is None:
                logger.debug("No ready_by_config record found")
                return None

            return CarHeaterReadyByConfig(
                id=1,
                config_json=row["config_json"],
                updated_ts=row["updated_ts"],
            )
        except Exception as e:
            logger.exception("Error getting ready_by_config: %s", e)
            raise

    def save_ready_by_config(self, config: CarHeaterReadyByConfig) -> None:
        """Save ready-by config (upsert singleton record)."""
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        logger.debug("save_ready_by_config called")

        try:
            # Use pg_insert with on_conflict_do_update for upsert behavior
            stmt = pg_insert(car_heater_ready_by_config).values(
                id=1,
                config_json=config.config_json,
                updated_ts=config.updated_ts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "config_json": stmt.excluded.config_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error saving ready_by_config: %s", e)
            raise
