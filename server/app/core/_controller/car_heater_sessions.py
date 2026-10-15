"""Permanent heating-attempt identity and driver evidence; no actuator calls."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from sqlalchemy import Engine

from sqlalchemy import func, insert, select, update

from ..models import CarHeaterSession
from ..schema import car_heater_session

logger = logging.getLogger(__name__)


def _session(row) -> CarHeaterSession:
    values = dict(row)
    values["snapshot"] = json.loads(values["snapshot"])
    values["outcome"] = json.loads(values["outcome"]) if values["outcome"] else None
    return CarHeaterSession(**values)


class CarHeaterSessionMixin:
    _sa_engine: Engine

    def create_car_heater_session(
        self,
        *,
        trigger: str,
        observed_at: str,
        requested_departure_at: str | None,
        heating_duration_minutes: float | None,
        snapshot: dict,
    ) -> CarHeaterSession:
        attempt = CarHeaterSession(
            id=str(uuid4()),
            trigger=trigger,
            created_at=datetime.now(UTC).isoformat(),
            observed_at=observed_at,
            requested_departure_at=requested_departure_at,
            heating_duration_minutes=heating_duration_minutes,
            snapshot=snapshot,
            outcome=None,
        )
        values = asdict(attempt)
        values["snapshot"] = json.dumps(snapshot)
        with self._sa_engine.begin() as conn:
            conn.execute(insert(car_heater_session).values(**values))
        logger.debug("Recorded heating attempt id=%s trigger=%s", attempt.id, trigger)
        return attempt

    def get_car_heater_session(self, session_id: str) -> CarHeaterSession | None:
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(
                    select(car_heater_session).where(car_heater_session.c.id == session_id)
                )
                .mappings()
                .first()
            )
        return _session(row) if row else None

    def list_car_heater_sessions(
        self, start_iso: str | None = None, end_iso: str | None = None
    ) -> list[CarHeaterSession]:
        session_time = func.coalesce(
            car_heater_session.c.requested_departure_at, car_heater_session.c.observed_at
        )
        stmt = select(car_heater_session)
        if start_iso is not None:
            stmt = stmt.where(session_time >= start_iso, session_time < end_iso)
        stmt = stmt.order_by(
            session_time.desc(),
            car_heater_session.c.created_at.desc(),
            car_heater_session.c.id.desc(),
        )
        with self._sa_engine.connect() as conn:
            return [_session(row) for row in conn.execute(stmt).mappings()]

    def save_car_heater_outcome(self, session_id: str, outcome: dict) -> CarHeaterSession | None:
        with self._sa_engine.begin() as conn:
            result = conn.execute(
                update(car_heater_session)
                .where(car_heater_session.c.id == session_id)
                .values(outcome=json.dumps(outcome))
            )
            if not result.rowcount:
                return None
        logger.debug("Saved departure outcome session=%s result=%s", session_id, outcome["result"])
        return self.get_car_heater_session(session_id)

    def save_car_heater_execution(self, execution: dict) -> None:
        from ..schema import car_heater_execution

        values = {
            "session_id": execution["session_id"],
            "active": execution["phase"] != "stopped",
            "payload": json.dumps(execution),
        }
        with self._sa_engine.begin() as conn:
            result = conn.execute(
                update(car_heater_execution)
                .where(car_heater_execution.c.session_id == execution["session_id"])
                .values(**values)
            )
            if not result.rowcount:
                conn.execute(insert(car_heater_execution).values(**values))

    def get_car_heater_execution(self, session_id: str | None = None) -> dict | None:
        from ..schema import car_heater_execution

        stmt = select(car_heater_execution.c.payload)
        if session_id is None:
            stmt = stmt.where(car_heater_execution.c.active.is_(True))
        else:
            stmt = stmt.where(car_heater_execution.c.session_id == session_id)
        with self._sa_engine.connect() as conn:
            payload = conn.execute(stmt).scalar()
        return json.loads(payload) if payload else None

    def get_departure_plan(self, session_id: str | None = None) -> dict | None:
        from ..schema import car_heater_departure_plan as plans

        stmt = select(plans.c.payload).where(
            plans.c.session_id == session_id if session_id else plans.c.is_current.is_(True)
        )
        with self._sa_engine.connect() as conn:
            payload = conn.execute(stmt).scalar()
        return json.loads(payload) if payload else None

    def create_departure_revision(self, plan: dict) -> None:
        """Atomically retain the old revision and create the new session/plan."""
        from ..schema import car_heater_departure_plan as plans

        with self._sa_engine.begin() as conn:
            previous = conn.execute(
                select(plans.c.payload).where(plans.c.is_current.is_(True))
            ).scalar()
            if previous:
                old = json.loads(previous)
                if old["status"] in ("scheduled", "waiting_for_device"):
                    old["status"] = "superseded"
                    old["events"].append(
                        {"at": plan["created_at"], "reason": "replaced", "kind": "superseded"}
                    )
                conn.execute(
                    update(plans)
                    .where(plans.c.session_id == old["session_id"])
                    .values(is_current=False, payload=json.dumps(old))
                )
            conn.execute(
                insert(car_heater_session).values(
                    id=plan["session_id"],
                    trigger="defrost",
                    created_at=plan["created_at"],
                    observed_at=plan["created_at"],
                    requested_departure_at=plan["departure_at"],
                    heating_duration_minutes=plan["preheat_minutes"],
                    snapshot=json.dumps(
                        {"planning_weather": plan["weather"], "software_version": "departure-v1"}
                    ),
                )
            )
            conn.execute(
                insert(plans).values(
                    session_id=plan["session_id"], is_current=True, payload=json.dumps(plan)
                )
            )

    def save_departure_plan(self, plan: dict) -> None:
        from ..schema import car_heater_departure_plan as plans

        with self._sa_engine.begin() as conn:
            conn.execute(
                update(plans)
                .where(plans.c.session_id == plan["session_id"])
                .values(payload=json.dumps(plan))
            )
