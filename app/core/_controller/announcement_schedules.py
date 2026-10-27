"""Announcement schedules and reminder occurrences; no Cast or synthesis calls.

Every state change is a conditional UPDATE inside one transaction so the
service loop and browser mutations (pause/delete versus materialize/dispatch)
cannot both win: whichever commits first decides, the other changes nothing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..models import AnnouncementAcknowledgement, AnnouncementOccurrence, AnnouncementSchedule
from ..schema import (
    announcement_acknowledgement,
    announcement_attempt,
    announcement_fallback,
    announcement_occurrence,
    announcement_schedule,
    announcement_snooze,
)
from .announcements import encode_attempt

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
            # The next instance replaces older pending speech in this same
            # transaction; no worker can see both as dispatchable.
            older = [
                occ.schedule_id == occurrence["schedule_id"],
                occ.instance_due_at < occurrence["instance_due_at"],
                occ.disposition.is_(None),
                occ.expires_at > occurrence["instance_due_at"],
            ]
            conn.execute(
                update(announcement_occurrence)
                .where(*older, occ.status == "waiting")
                .values(
                    status="closed",
                    disposition="superseded",
                    disposition_reason="next_occurrence",
                    next_attempt_at=None,
                    next_attempt_purpose=None,
                    waiting_reason=None,
                    closed_at=occurrence["created_at"],
                    updated_at=occurrence["created_at"],
                )
            )
            conn.execute(
                update(announcement_occurrence)
                .where(*older, occ.status == "announcing")
                .values(
                    disposition="superseded",
                    disposition_reason="next_occurrence",
                    next_attempt_at=None,
                    next_attempt_purpose=None,
                    updated_at=occurrence["created_at"],
                )
            )
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
        snapshot: dict | None = None,
        **values,
    ) -> bool:
        stmt = update(announcement_occurrence).where(announcement_occurrence.c.id == occurrence_id)
        if expected is not None:
            stmt = stmt.where(announcement_occurrence.c.status.in_(list(expected)))
        for key, value in (snapshot or {}).items():
            column = announcement_occurrence.c[key]
            stmt = stmt.where(column.is_(None) if value is None else column == value)
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
        expected_due_at: str | None = None,
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
        if expected_due_at is not None:
            conditions.append(occ.due_at == expected_due_at)
        if not_expired_at is not None:
            sched = announcement_schedule.c
            conditions.extend(
                [
                    occ.expires_at > not_expired_at,
                    occ.next_attempt_at <= not_expired_at,
                    exists().where(
                        sched.id == occ.schedule_id,
                        sched.status == "active",
                        sched.next_due_at.is_(None) | (sched.next_due_at > not_expired_at),
                    ),
                ]
            )
        row = encode_attempt(attempt)
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

    # Acknowledgements and Stop -------------------------------------------------
    def acknowledge_announcement_occurrence(
        self, occurrence_id: str, *, action: str, ack_id: str, at: str, by: str | None
    ) -> tuple[str, AnnouncementAcknowledgement | None]:
        """Record Done/Skip once per occurrence and end its automatic speech.

        Returns ("created" | "exists" | "conflict" | "not_found", acknowledgement).
        In the same transaction a waiting occurrence closes (pending speech,
        repeats and retries never start: dispatch requires no disposition), an
        announcing one keeps its current speech but gets no follow-up, and a
        fallback still pending (no request in flight) is cancelled. Delivery
        evidence and sent/uncertain fallbacks are left as they were.
        """
        occ = announcement_occurrence.c
        try:
            with self._sa_engine.begin() as conn:
                current = (
                    conn.execute(
                        select(announcement_occurrence)
                        .where(occ.id == occurrence_id)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    return "not_found", None
                existing = self._acknowledgement(conn, occurrence_id)
                if existing is not None:
                    return ("exists" if existing.action == action else "conflict"), existing
                conn.execute(
                    insert(announcement_acknowledgement).values(
                        id=ack_id,
                        occurrence_id=occurrence_id,
                        action=action,
                        acknowledged_at=at,
                        acknowledged_by=by,
                        prior_status=current["status"],
                        prior_delivery=current["delivery"],
                        prior_disposition=current["disposition"],
                        prior_disposition_reason=current["disposition_reason"],
                        announcements_started=current["announcements_started"],
                    )
                )
                marked = {"disposition": action, "disposition_reason": "acknowledged"}
                closed = conn.execute(
                    update(announcement_occurrence)
                    .where(occ.id == occurrence_id, occ.status == "waiting")
                    .values(
                        status="closed",
                        waiting_reason=None,
                        next_attempt_at=None,
                        next_attempt_purpose=None,
                        closed_at=at,
                        updated_at=at,
                        **marked,
                    )
                ).rowcount
                if not closed:
                    conn.execute(
                        update(announcement_occurrence)
                        .where(occ.id == occurrence_id)
                        .values(updated_at=at, **marked)
                    )
                fb = announcement_fallback.c
                conn.execute(
                    update(announcement_fallback)
                    .where(fb.occurrence_id == occurrence_id, fb.status == "pending")
                    .values(
                        status="cancelled",
                        status_reason="acknowledged",
                        next_attempt_at=None,
                        completed_at=at,
                        updated_at=at,
                    )
                )
        except IntegrityError:
            with self._sa_engine.connect() as conn:
                existing = self._acknowledgement(conn, occurrence_id)
            if existing is None:
                raise
            return ("exists" if existing.action == action else "conflict"), existing
        logger.debug("Occurrence %s acknowledged action=%s", occurrence_id, action)
        return "created", self.get_announcement_acknowledgement(occurrence_id)

    @staticmethod
    def _acknowledgement(conn, occurrence_id: str) -> AnnouncementAcknowledgement | None:
        row = (
            conn.execute(
                select(announcement_acknowledgement).where(
                    announcement_acknowledgement.c.occurrence_id == occurrence_id
                )
            )
            .mappings()
            .first()
        )
        return AnnouncementAcknowledgement(**row) if row else None

    def get_announcement_acknowledgement(
        self, occurrence_id: str
    ) -> AnnouncementAcknowledgement | None:
        with self._sa_engine.connect() as conn:
            return self._acknowledgement(conn, occurrence_id)

    def list_announcement_acknowledgements(
        self, occurrence_ids: Iterable[str]
    ) -> list[AnnouncementAcknowledgement]:
        ids = list(occurrence_ids)
        if not ids:
            return []
        stmt = select(announcement_acknowledgement).where(
            announcement_acknowledgement.c.occurrence_id.in_(ids)
        )
        with self._sa_engine.connect() as conn:
            return [AnnouncementAcknowledgement(**row) for row in conn.execute(stmt).mappings()]

    def stop_announcement_occurrence(self, occurrence_id: str, *, at: str) -> str | None:
        """Cancel further automatic speech without acknowledging (Stop speaking).

        An announcing occurrence keeps its status (the service settles it from
        the attempt's actual stop outcome); a waiting one with speech already
        started (a pending repeat or retry) closes. Either way it gets
        disposition 'stopped' unless it already has one, and never a fallback.
        Returns the occurrence status it applied to, or None if nothing changed.
        """
        occ = announcement_occurrence.c
        marked = {"disposition": "stopped", "disposition_reason": "stop_requested"}
        with self._sa_engine.begin() as conn:
            if conn.execute(
                update(announcement_occurrence)
                .where(
                    occ.id == occurrence_id,
                    occ.status == "announcing",
                    occ.disposition.is_(None),
                )
                .values(updated_at=at, **marked)
            ).rowcount:
                return "announcing"
            if conn.execute(
                update(announcement_occurrence)
                .where(
                    occ.id == occurrence_id,
                    occ.status == "waiting",
                    occ.disposition.is_(None),
                    (occ.announcements_started + occ.retries_started) > 0,
                )
                .values(
                    status="closed",
                    waiting_reason=None,
                    next_attempt_at=None,
                    next_attempt_purpose=None,
                    closed_at=at,
                    updated_at=at,
                    **marked,
                )
            ).rowcount:
                return "waiting"
        return None

    def has_newer_announcement_occurrence(self, schedule_id: str, instance_due_at: str) -> bool:
        """Use the schedule-instance index without loading its entire history."""
        occ = announcement_occurrence.c
        with self._sa_engine.connect() as conn:
            return bool(
                conn.execute(
                    select(
                        exists().where(
                            occ.schedule_id == schedule_id, occ.instance_due_at > instance_due_at
                        )
                    )
                ).scalar()
            )

    def list_announcement_snoozes(self, occurrence_ids):
        with self._sa_engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(announcement_snooze)
                    .where(announcement_snooze.c.occurrence_id.in_(list(occurrence_ids)))
                    .order_by(announcement_snooze.c.snoozed_at, announcement_snooze.c.request_id)
                ).mappings()
            ]

    def snooze_announcement_occurrence(self, row: dict, *, expected_due_at: str) -> str:
        try:
            return self._snooze_announcement_occurrence(row, expected_due_at=expected_due_at)
        except IntegrityError:
            # A request id may race across different schedules (and therefore
            # different schedule locks). Preserve the first committed action.
            with self._sa_engine.connect() as conn:
                existing = (
                    conn.execute(
                        select(announcement_snooze).where(
                            announcement_snooze.c.request_id == row["request_id"]
                        )
                    )
                    .mappings()
                    .first()
                )
            if existing is None:
                raise
            return self._same_snooze(existing, row)

    @staticmethod
    def _same_snooze(existing, row):
        return (
            "exists"
            if (
                existing["occurrence_id"] == row["occurrence_id"]
                and existing["minutes"] == row["minutes"]
            )
            else "conflict"
        )

    def _snooze_announcement_occurrence(self, row: dict, *, expected_due_at: str) -> str:
        """Postpone and stop existing speech atomically, preserving all counters.

        Lock the schedule before the occurrence, matching materialization's
        order. A newer recurrence or a pause/delete wins even when the worker
        has not materialized its next instance yet.
        """
        occ = announcement_occurrence.c
        sched = announcement_schedule.c
        with self._sa_engine.begin() as conn:
            existing = (
                conn.execute(
                    select(announcement_snooze).where(
                        announcement_snooze.c.request_id == row["request_id"]
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return self._same_snooze(existing, row)
            schedule_id = conn.execute(
                select(occ.schedule_id).where(occ.id == row["occurrence_id"])
            ).scalar_one_or_none()
            schedule = (
                conn.execute(
                    select(announcement_schedule).where(sched.id == schedule_id).with_for_update()
                )
                .mappings()
                .first()
            )
            existing = (
                conn.execute(
                    select(announcement_snooze).where(
                        announcement_snooze.c.request_id == row["request_id"]
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return self._same_snooze(existing, row)
            current = (
                conn.execute(
                    select(announcement_occurrence)
                    .where(occ.id == row["occurrence_id"])
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if current is None:
                return "not_found"
            newer = conn.execute(
                select(occ.id)
                .where(
                    occ.schedule_id == schedule_id, occ.instance_due_at > current["instance_due_at"]
                )
                .limit(1)
            ).first()
            if (
                newer
                or schedule["status"] != "active"
                or current["disposition"] not in (None, "stopped")
                or current["due_at"] != expected_due_at
                or (schedule["next_due_at"] and schedule["next_due_at"] <= row["snoozed_at"])
            ):
                return "ineligible"
            changed = conn.execute(
                update(announcement_occurrence)
                .where(
                    occ.id == row["occurrence_id"],
                    occ.due_at == expected_due_at,
                    occ.disposition.is_(None) | (occ.disposition == "stopped"),
                )
                .values(
                    status="waiting",
                    disposition=None,
                    disposition_reason=None,
                    due_at=row["due_at"],
                    expires_at=row["expires_at"],
                    next_attempt_at=row["due_at"],
                    next_attempt_purpose="snooze",
                    waiting_reason=None,
                    waiting_since=None,
                    current_attempt_id=None,
                    closed_at=None,
                    updated_at=row["snoozed_at"],
                )
            ).rowcount
            if not changed:
                return "ineligible"
            row["prior_disposition"] = current["disposition"]
            conn.execute(insert(announcement_snooze).values(**row))
            conn.execute(
                update(announcement_attempt)
                .where(
                    announcement_attempt.c.occurrence_id == row["occurrence_id"],
                    announcement_attempt.c.status.in_(("dispatching", "dispatched", "playing")),
                    announcement_attempt.c.stop_requested_at.is_(None),
                )
                .values(stop_requested_at=row["snoozed_at"], updated_at=row["snoozed_at"])
            )
        return "created"
