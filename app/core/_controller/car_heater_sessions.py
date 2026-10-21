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
from sqlalchemy.exc import IntegrityError

from ..models import CarHeaterPhoto, CarHeaterSession
from ..schema import car_heater_observation, car_heater_photo, car_heater_session
from .car_heater_installation import installation_snapshot

logger = logging.getLogger(__name__)


def _session(row) -> CarHeaterSession:
    values = dict(row)
    values["snapshot"] = json.loads(values["snapshot"])
    values["outcome"] = json.loads(values["outcome"]) if values["outcome"] else None
    return CarHeaterSession(**values)


_PHOTO_FIELDS = [c for c in car_heater_photo.c if c.name != "content"]


def _session_exists(conn, session_id: str, *, lock: bool = False) -> bool:
    stmt = select(car_heater_session.c.id).where(car_heater_session.c.id == session_id)
    return conn.scalar(stmt.with_for_update() if lock else stmt) is not None


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
                        {
                            "planning_weather": plan["weather"],
                            "software_version": "departure-v1",
                            "installation": installation_snapshot(conn),
                        }
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

    def save_car_heater_photo(
        self, photo: CarHeaterPhoto, content: bytes, *, max_per_session: int
    ) -> tuple[str, CarHeaterPhoto | None]:
        """Persist once per client photo id; a returned photo is committed content.

        Returns ("created" | "exists", photo), or ("conflict" | "missing_session"
        | "limit", None). A retried upload of identical content is "exists".
        """
        values = {**asdict(photo), "content": content}
        try:
            with self._sa_engine.begin() as conn:
                # The row lock serializes uploads per session on PostgreSQL, so
                # the per-session count check below cannot be raced past.
                if not _session_exists(conn, photo.session_id, lock=True):
                    return "missing_session", None
                existing = self._photo_row(conn, photo.id)
                if existing is not None:
                    return self._same_photo(existing, photo)
                count = conn.scalar(
                    select(func.count())
                    .select_from(car_heater_photo)
                    .where(car_heater_photo.c.session_id == photo.session_id)
                )
                if count >= max_per_session:
                    return "limit", None
                conn.execute(insert(car_heater_photo).values(**values))
        except IntegrityError:
            # A concurrent retry committed the same client id first.
            with self._sa_engine.connect() as conn:
                existing = self._photo_row(conn, photo.id)
            if existing is None:
                raise
            return self._same_photo(existing, photo)
        logger.debug(
            "Saved observation photo session=%s photo=%s kind=%s bytes=%d",
            photo.session_id,
            photo.id,
            photo.capture_kind,
            photo.byte_size,
        )
        return "created", photo

    @staticmethod
    def _photo_row(conn, photo_id: str) -> CarHeaterPhoto | None:
        row = (
            conn.execute(select(*_PHOTO_FIELDS).where(car_heater_photo.c.id == photo_id))
            .mappings()
            .first()
        )
        return CarHeaterPhoto(**row) if row else None

    @staticmethod
    def _same_photo(existing: CarHeaterPhoto, photo: CarHeaterPhoto):
        if existing.session_id == photo.session_id and existing.sha256 == photo.sha256:
            return "exists", existing
        logger.warning("Rejected reused photo id=%s for different content", photo.id)
        return "conflict", None

    def list_car_heater_photos(self, session_id: str) -> list[CarHeaterPhoto]:
        stmt = (
            select(*_PHOTO_FIELDS)
            .where(car_heater_photo.c.session_id == session_id)
            .order_by(car_heater_photo.c.captured_at, car_heater_photo.c.id)
        )
        with self._sa_engine.connect() as conn:
            return [CarHeaterPhoto(**row) for row in conn.execute(stmt).mappings()]

    def get_car_heater_photo_content(
        self, session_id: str, photo_id: str
    ) -> tuple[str, bytes] | None:
        stmt = select(car_heater_photo.c.content_type, car_heater_photo.c.content).where(
            car_heater_photo.c.session_id == session_id, car_heater_photo.c.id == photo_id
        )
        with self._sa_engine.connect() as conn:
            row = conn.execute(stmt).first()
        return (row.content_type, bytes(row.content)) if row else None

    def summarize_car_heater_photos(self, session_ids: list[str]) -> dict[str, dict]:
        stmt = (
            select(
                car_heater_photo.c.session_id,
                func.count().label("saved"),
                func.min(car_heater_photo.c.captured_at).label("first_captured_at"),
                func.max(car_heater_photo.c.captured_at).label("last_captured_at"),
            )
            .where(car_heater_photo.c.session_id.in_(session_ids))
            .group_by(car_heater_photo.c.session_id)
        )
        with self._sa_engine.connect() as conn:
            return {
                row["session_id"]: {
                    "saved": row["saved"],
                    "first_captured_at": row["first_captured_at"],
                    "last_captured_at": row["last_captured_at"],
                }
                for row in conn.execute(stmt).mappings()
            }

    def save_car_heater_observation(
        self, observation_id: str, session_id: str, started_at: str, finished_at, missed: list
    ) -> tuple[str, dict | None]:
        """Create or merge a capture window. Never drops misses or clears a finish.

        Returns ("created" | "updated", record), or ("conflict" | "missing_session", None).
        """
        table = car_heater_observation
        now = datetime.now(UTC).isoformat()
        with self._sa_engine.begin() as conn:
            if not _session_exists(conn, session_id):
                return "missing_session", None
            row = conn.execute(select(table).where(table.c.id == observation_id)).mappings().first()
            if row is None:
                conn.execute(
                    insert(table).values(
                        id=observation_id,
                        session_id=session_id,
                        started_at=started_at,
                        finished_at=finished_at,
                        missed=json.dumps(missed),
                        updated_at=now,
                    )
                )
                result = "created"
            else:
                if row["session_id"] != session_id or row["started_at"] != started_at:
                    return "conflict", None
                merged = {m["sequence"]: m for m in json.loads(row["missed"])}
                for item in missed:
                    merged.setdefault(item["sequence"], item)
                conn.execute(
                    update(table)
                    .where(table.c.id == observation_id)
                    .values(
                        finished_at=row["finished_at"] or finished_at,
                        missed=json.dumps([merged[k] for k in sorted(merged)]),
                        updated_at=now,
                    )
                )
                result = "updated"
        logger.debug(
            "Saved observation window session=%s observation=%s finished=%s missed=%d",
            session_id,
            observation_id,
            bool(finished_at),
            len(missed),
        )
        return result, next(
            o for o in self.list_car_heater_observations(session_id) if o["id"] == observation_id
        )

    def list_car_heater_observations(self, session_id: str) -> list[dict]:
        table = car_heater_observation
        stmt = (
            select(table.c.id, table.c.started_at, table.c.finished_at, table.c.missed)
            .where(table.c.session_id == session_id)
            .order_by(table.c.started_at, table.c.id)
        )
        with self._sa_engine.connect() as conn:
            return [
                {**row, "missed": json.loads(row["missed"])}
                for row in conn.execute(stmt).mappings()
            ]
