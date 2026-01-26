import logging

from ..models import CarHeaterReadyByConfig, CarHeaterReadyByState

logger = logging.getLogger(__name__)


class CarHeaterReadyByMixin:
    # --- Ready-by state (single-row settings) ---
    def get_ready_by_state(self) -> CarHeaterReadyByState | None:
        row = self.db.fetchone(
            """
            SELECT state_json, updated_ts
              FROM car_heater_ready_by_state
             WHERE id = 1
            """
        )
        if row is None:
            return None
        return CarHeaterReadyByState(
            id=1,
            state_json=row["state_json"],
            updated_ts=row["updated_ts"],
        )

    def save_ready_by_state(self, state: CarHeaterReadyByState) -> None:
        self.db.execute_query(
            """
            INSERT INTO car_heater_ready_by_state (id, state_json, updated_ts)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_ts = excluded.updated_ts
            """,
            (
                state.state_json,
                state.updated_ts,
            ),
        )

    # --- Ready-by config (single-row settings) ---
    def get_ready_by_config(self) -> CarHeaterReadyByConfig | None:
        row = self.db.fetchone(
            """
            SELECT config_json, updated_ts
              FROM car_heater_ready_by_config
             WHERE id = 1
            """
        )
        if row is None:
            return None
        return CarHeaterReadyByConfig(
            id=1,
            config_json=row["config_json"],
            updated_ts=row["updated_ts"],
        )

    def save_ready_by_config(self, config: CarHeaterReadyByConfig) -> None:
        self.db.execute_query(
            """
            INSERT INTO car_heater_ready_by_config (id, config_json, updated_ts)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                config_json = excluded.config_json,
                updated_ts = excluded.updated_ts
            """,
            (
                config.config_json,
                config.updated_ts,
            ),
        )
