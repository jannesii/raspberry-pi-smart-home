"""Owner-recorded car-heater installation with configuration history."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, insert, select
from sqlalchemy.exc import SQLAlchemyError

from ..schema import car_heater_installation

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

INSTALLATION_FIELDS = (
    "heater",
    "power_topology",
    "glass_reference_position",
    "glass_comparison_position",
    "cabin_position",
)


def _newest(conn):
    return (
        conn.execute(
            select(car_heater_installation).order_by(desc(car_heater_installation.c.id)).limit(1)
        )
        .mappings()
        .first()
    )


def installation_snapshot(conn) -> dict[str, Any] | None:
    """The current record as copied into a new session's immutable snapshot.

    Session creation must not depend on this optional record: if it cannot be
    read (for example migration 20261021_0019 not applied yet), the session is
    created without it. A savepoint keeps PostgreSQL's transaction usable.
    """
    try:
        with conn.begin_nested():
            row = _newest(conn)
    except SQLAlchemyError:
        logger.warning(
            "Installation record unreadable; session snapshot records none", exc_info=True
        )
        return None
    if row is None:
        return None
    return {
        "record_id": row["id"],
        "saved_at": row["saved_at"],
        **{f: row[f] for f in INSTALLATION_FIELDS},
    }


class CarHeaterInstallationMixin:
    _sa_engine: Engine

    def get_car_heater_installation_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Saved installation records, newest (current) first."""
        stmt = (
            select(car_heater_installation)
            .order_by(desc(car_heater_installation.c.id))
            .limit(limit)
        )
        with self._sa_engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings()]

    def get_car_heater_installation_snapshot(self) -> dict[str, Any] | None:
        with self._sa_engine.connect() as conn:
            return installation_snapshot(conn)

    def save_car_heater_installation(
        self, changes: dict[str, str | None], *, saved_by: str | None
    ) -> dict[str, Any]:
        """Append a record merging ``changes`` over the current one; returns it."""
        with self._sa_engine.begin() as conn:
            current = _newest(conn)
            values = {f: current[f] if current else None for f in INSTALLATION_FIELDS}
            values.update(changes)
            values["saved_at"] = datetime.now(UTC).isoformat()
            values["saved_by"] = saved_by
            new_id = conn.execute(
                insert(car_heater_installation)
                .values(**values)
                .returning(car_heater_installation.c.id)
            ).scalar_one()
        logger.info("Car heater installation record %s saved fields=%s", new_id, sorted(changes))
        return {"id": new_id, **values}
