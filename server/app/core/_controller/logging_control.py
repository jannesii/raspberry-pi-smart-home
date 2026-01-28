from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..schema import logging_control as log_ctrl_table

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class LoggingControlMixin:
    def get_logging_control_config(self) -> dict[str, Any] | None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = select(log_ctrl_table.c.config_json).where(log_ctrl_table.c.id == 1)

            with sa_engine.connect() as conn:
                raw = conn.execute(stmt).scalar_one_or_none()  # returns str | None

            if not raw:
                return None

            cfg = json.loads(raw)
            return cfg if isinstance(cfg, dict) else None

        except Exception as e:
            logger.exception("Error fetching logging control config %s", e)
            return None

    def set_logging_control_config(self, config: dict[str, Any]) -> None:
        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]
        raw = json.dumps(config, ensure_ascii=False, separators=(",", ":"))

        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        row = None
        try:
            stmt = (
                pg_insert(log_ctrl_table)
                .values(id=1, config_json=raw, updated_ts=now)
                .on_conflict_do_update(
                    index_elements=[log_ctrl_table.c.id],  # conflict target
                    set_={
                        "config_json": raw,
                        "updated_ts": now,
                    },
                )
                .returning(log_ctrl_table.c.updated_ts)
            )

            with sa_engine.begin() as conn:
                row = conn.execute(stmt).mappings().first()  # dict-like row or None

        except Exception as e:
            logger.exception("Error setting logging control config %s", e)
            return

        if row:
            logger.debug("Logging control config updated at %s", row["updated_ts"])
        else:
            logger.debug("Logging control config updated (no timestamp returned)")

    def clear_logging_control_config(self) -> None:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")

        try:
            stmt = log_ctrl_table.delete().where(log_ctrl_table.c.id == 1)
            with sa_engine.begin() as conn:
                conn.execute(stmt)
        except Exception as e:
            logger.exception("Error clearing logging control config %s", e)
