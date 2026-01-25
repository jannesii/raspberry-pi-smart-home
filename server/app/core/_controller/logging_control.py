from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class LoggingControlMixin:
    def get_logging_control_config(self) -> dict[str, Any] | None:
        row = self.db.fetchone(  # type: ignore[attr-defined]
            "SELECT config_json FROM logging_control WHERE id = 1"
        )
        if not row:
            return None
        try:
            raw = row["config_json"]
        except Exception:
            raw = None
        if not raw:
            return None
        try:
            cfg = json.loads(raw)
            return cfg if isinstance(cfg, dict) else None
        except Exception:
            return None

    def set_logging_control_config(self, config: dict[str, Any]) -> None:
        now = datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]
        raw = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        self.db.execute_query(  # type: ignore[attr-defined]
            """
            INSERT INTO logging_control (id, config_json, updated_ts)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              config_json = excluded.config_json,
              updated_ts = excluded.updated_ts
            """,
            (raw, now),
        )

    def clear_logging_control_config(self) -> None:
        self.db.execute_query(  # type: ignore[attr-defined]
            "DELETE FROM logging_control WHERE id = 1"
        )
