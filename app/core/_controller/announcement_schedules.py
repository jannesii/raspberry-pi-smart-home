"""Announcement schedules and reminder occurrences; no Cast or synthesis calls.

Every state change is a conditional UPDATE inside one transaction so the
service loop and browser mutations (pause/delete versus materialize/dispatch)
cannot both win: whichever commits first decides, the other changes nothing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..models import AnnouncementOccurrence, AnnouncementSchedule
from ..schema import announcement_attempt, announcement_occurrence, announcement_schedule

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

# Fields whose equality makes a repeated create request "the same request".
_SCHEDULE_FIELDS = (
    "kind",
    "text",
    "language",
    "target_key",
    "volume_percent",
    "chime_percent",
    "urgent",
    "quiet_hours_override",
    "expiry_minutes",
    "max_announcements",
    "repeat_interval_minutes",
    "due_local",
    "due_fold",
    "recurrence",
)


def _schedule(row) -> AnnouncementSchedule:
    values = dict(row)
    values["recurrence"] = json.loads(values["recurrence"]) if values["recurrence"] else None
    values["urgent"] = bool(values["urgent"])
    values["quiet_hours_override"] = bool(values["quiet_hours_override"])
    return AnnouncementSchedule(**values)


def _occurrence(row) -> AnnouncementOccurrence:
    values = dict(row)
    values["speaker_set"] = json.loads(values["speaker_set"])
    values["waiting_history"] = json.loads(values["waiting_history"] or "[]")
    values["urgent"] = bool(values["urgent"])
    return AnnouncementOccurrence(**values)


def _encode_schedule(values: dict) -> dict:
    row = dict(values)
    if "recurrence" in row:
        row["recurrence"] = json.dumps(row["recurrence"]) if row["recurrence"] else None
    return row


def _encode_occurrence(values: dict) -> dict:
    row = dict(values)
    if "speaker_set" in row:
        row["speaker_set"] = json.dumps(sorted(row["speaker_set"]))
    if "waiting_history" in row:
        row["waiting_history"] = json.dumps(row["waiting_history"])
    return row


class AnnouncementScheduleMixin:
    _sa_engine: Engine

    # Schedules ----------------------------------------------------------------
    def create_announcement_schedule(self, row: dict) -> tuple[str, AnnouncementSchedule]:
        """Insert a schedule, idempotent on ``request_id``.

        Returns ("created" | "exists" | "conflict", schedule).
        """
        try:
            with self._sa_engine.begin() as conn:
                conn.execute(insert(announcement_schedule).values(**_encode_schedule(row)))
        except IntegrityError:
            existing = (
                self.get_announcement_schedule_by_request(row["request_id"])
                if row.get("request_id")
                else None
            )
            if existing is None:
                raise
            same = all(getattr(existing, f) == row[f] for f in _SCHEDULE_FIELDS)
            logger.debug("Repeated schedule request same_payload=%s", same)
            return ("exists" if same else "conflict"), existing
        return "created", self.get_announcement_schedule(row["id"])

    def _one_schedule(self, clause) -> AnnouncementSchedule | None:
        with self._sa_engine.connect() as conn:
            row = conn.execute(select(announcement_schedule).where(clause)).mappings().first()
        return _schedule(row) if row else None

    def get_announcement_schedule(self, schedule_id: str) -> AnnouncementSchedule | None:
        return self._one_schedule(announcement_schedule.c.id == schedule_id)

    def get_announcement_schedule_by_request(self, request_id: str):
        return self._one_schedule(announcement_schedule.c.request_id == request_id)

    def list_announcement_schedules(
        self, *, include_deleted: bool = False
    ) -> list[AnnouncementSchedule]:
        stmt = select(announcement_schedule)
        if not include_deleted:
            stmt = stmt.where(announcement_schedule.c.status != "deleted")
        stmt = stmt.order_by(
            announcement_schedule.c.next_due_at.is_(None),
            announcement_schedule.c.next_due_at,
            announcement_schedule.c.created_at,
            announcement_schedule.c.id,
        )
        with self._sa_engine.connect() as conn:
            return [_schedule(row) for row in conn.execute(stmt).mappings()]

    def list_due_announcement_schedules(self, now_iso: str) -> list[AnnouncementSchedule]:
        stmt = (
            select(announcement_schedule)
            .where(
                announcement_schedule.c.status == "active",
                announcement_schedule.c.next_due_at.is_not(None),
                announcement_schedule.c.next_due_at <= now_iso,
            )
            .order_by(announcement_schedule.c.next_due_at, announcement_schedule.c.id)
        )
        with self._sa_engine.connect() as conn:
            return [_schedule(row) for row in conn.execute(stmt).mappings()]

    def update_announcement_schedule(
        self,
        schedule_id: str,
        *,
        expected_status: Iterable[str] | None = None,
        expected_revision: int | None = None,
        cancel_reason: str | None = None,
        **values,
    ) -> bool:
        """Conditionally update a schedule.

        With ``cancel_reason`` the same transaction closes the schedule's
        waiting occurrences as ``cancelled`` and marks announcing ones
        cancelled so they get no further automatic speech (current speech is
        not cut off). Cancellation never sets a fallback reason.
        """
        stmt = update(announcement_schedule).where(announcement_schedule.c.id == schedule_id)
        if expected_status is not None:
            stmt = stmt.where(announcement_schedule.c.status.in_(list(expected_status)))
        if expected_revision is not None:
            stmt = stmt.where(announcement_schedule.c.revision == expected_revision)
        now = values.get("updated_at")
        with self._sa_engine.begin() as conn:
            changed = conn.execute(stmt.values(**_encode_schedule(values))).rowcount == 1
            if changed and cancel_reason:
                occ = announcement_occurrence.c
                conn.execute(
                    update(announcement_occurrence)
                    .where(occ.schedule_id == schedule_id, occ.status == "waiting")
                    .values(
                        status="closed",
                        waiting_reason=None,
                        disposition="cancelled",
                        disposition_reason=cancel_reason,
                        closed_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    update(announcement_occurrence)
                    .where(
                        occ.schedule_id == schedule_id,
                        occ.status == "announcing",
                        occ.disposition.is_(None),
                    )
                    .values(
                        disposition="cancelled", disposition_reason=cancel_reason, updated_at=now
                    )
                )
        return changed

    # Occurrences ----------------------------------------------------------------
    def materialize_announcement_occurrence(
        self, occurrence: dict, *, next_due_at: str | None
    ) -> bool:
        """Create the occurrence for a due instance and advance the schedule.

        Both happen only while the schedule is still active and still points at
        this instance, so a concurrent pause/delete/edit or a second tick wins
        or loses as a whole. The (schedule, instance) unique constraint is the
        last line of defence against duplicates.
        """
        sched = announcement_schedule.c
        occ = announcement_occurrence.c
        with self._sa_engine.begin() as conn:
            advanced = conn.execute(
                update(announcement_schedule)
                .where(
                    sched.id == occurrence["schedule_id"],
                    sched.status == "active",
                    sched.next_due_at == occurrence["instance_due_at"],
                )
                .values(next_due_at=next_due_at)
            ).rowcount
            if advanced != 1:
                return False
            exists = conn.execute(
                select(occ.id).where(
                    occ.schedule_id == occurrence["schedule_id"],
                    occ.instance_due_at == occurrence["instance_due_at"],
                )
            ).first()
            if exists:
                logger.warning(
                    "Occurrence for schedule %s at %s already existed; not recreated",
                    occurrence["schedule_id"],
                    occurrence["instance_due_at"],
                )
                return False
            conn.execute(insert(announcement_occurrence).values(**_encode_occurrence(occurrence)))
        return True

    def get_announcement_occurrence(self, occurrence_id: str) -> AnnouncementOccurrence | None:
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(
                    select(announcement_occurrence).where(
                        announcement_occurrence.c.id == occurrence_id
                    )
                )
                .mappings()
                .first()
            )
        return _occurrence(row) if row else None

    def list_announcement_occurrences(
        self,
        *,
        statuses: Iterable[str] | None = None,
        schedule_ids: Iterable[str] | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[AnnouncementOccurrence]:
        occ = announcement_occurrence.c
        stmt = select(announcement_occurrence)
        if statuses is not None:
            stmt = stmt.where(occ.status.in_(list(statuses)))
        if schedule_ids is not None:
            stmt = stmt.where(occ.schedule_id.in_(list(schedule_ids)))
        if newest_first:
            stmt = stmt.order_by(occ.due_at.desc(), occ.id.desc())
        else:
            stmt = stmt.order_by(occ.due_at, occ.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._sa_engine.connect() as conn:
            return [_occurrence(row) for row in conn.execute(stmt).mappings()]

    def update_announcement_occurrence(
        self,
        occurrence_id: str,
        *,
        expected: Iterable[str] | None = None,
        **values,
    ) -> bool:
        stmt = update(announcement_occurrence).where(announcement_occurrence.c.id == occurrence_id)
        if expected is not None:
            stmt = stmt.where(announcement_occurrence.c.status.in_(list(expected)))
        with self._sa_engine.begin() as conn:
            changed = conn.execute(stmt.values(**_encode_occurrence(values))).rowcount == 1
        if changed and "status" in values:
            logger.debug(
                "Announcement occurrence %s -> %s delivery=%s disposition=%s",
                occurrence_id,
                values["status"],
                values.get("delivery"),
                values.get("disposition"),
            )
        return changed

    def add_announcement_occurrence_attempt(
        self,
        occurrence_id: str,
        attempt: dict,
        *,
        expected: Iterable[str] = ("waiting",),
        not_expired_at: str | None = None,
        **values,
    ) -> bool:
        """Record an attempt and the occurrence transition atomically.

        The occurrence must still be in ``expected`` status, have no
        disposition (not cancelled meanwhile) and, with ``not_expired_at``, not
        be expired at that instant. Dispatch intent for a ``dispatching``
        attempt is therefore durable before any Cast effect, and a lost race
        leaves no attempt behind.
        """
        occ = announcement_occurrence.c
        conditions = [
            occ.id == occurrence_id,
            occ.status.in_(list(expected)),
            occ.disposition.is_(None),
        ]
        if not_expired_at is not None:
            conditions.append(occ.expires_at > not_expired_at)
        row = dict(attempt)
        row["speaker_set"] = json.dumps(sorted(attempt["speaker_set"]))
        with self._sa_engine.begin() as conn:
            changed = conn.execute(
                update(announcement_occurrence)
                .where(and_(*conditions))
                .values(**_encode_occurrence(values))
            ).rowcount
            if changed != 1:
                return False
            conn.execute(insert(announcement_attempt).values(**row))
        return True
