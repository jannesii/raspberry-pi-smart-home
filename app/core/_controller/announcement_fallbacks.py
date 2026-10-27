"""Fallback notifications of reminder occurrences; no webhook calls here.

The unique ``occurrence_id`` makes one logical fallback per occurrence. Each
send is preceded by a committed attempt row and a conditional fallback
transition, so competing ticks or a restart can neither send twice for the
same attempt number nor reset the attempt budget.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import exists, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..models import AnnouncementFallback, AnnouncementFallbackAttempt
from ..schema import (
    announcement_acknowledgement,
    announcement_fallback,
    announcement_fallback_attempt,
    announcement_occurrence,
)
from .announcement_schedules import _occurrence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import Engine

    from ..models import AnnouncementOccurrence

logger = logging.getLogger(__name__)


class AnnouncementFallbackMixin:
    _sa_engine: Engine

    def list_announcement_occurrences_without_fallback(self) -> list[AnnouncementOccurrence]:
        """Fallback-eligible occurrences that have no fallback record yet."""
        occ = announcement_occurrence.c
        stmt = (
            select(announcement_occurrence)
            .where(
                occ.fallback_reason.is_not(None),
                ~exists().where(announcement_fallback.c.occurrence_id == occ.id),
            )
            .order_by(occ.fallback_eligible_at, occ.id)
        )
        with self._sa_engine.connect() as conn:
            return [_occurrence(row) for row in conn.execute(stmt).mappings()]

    def create_announcement_fallback(self, row: dict) -> bool:
        """Insert the occurrence's fallback; False if it already has one."""
        try:
            with self._sa_engine.begin() as conn:
                conn.execute(insert(announcement_fallback).values(**row))
        except IntegrityError:
            logger.debug("Fallback for occurrence %s already exists", row["occurrence_id"])
            return False
        return True

    def list_announcement_fallbacks(
        self,
        *,
        statuses: Iterable[str] | None = None,
        occurrence_ids: Iterable[str] | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[AnnouncementFallback]:
        fb = announcement_fallback.c
        stmt = select(announcement_fallback)
        if statuses is not None:
            stmt = stmt.where(fb.status.in_(list(statuses)))
        if occurrence_ids is not None:
            stmt = stmt.where(fb.occurrence_id.in_(list(occurrence_ids)))
        if newest_first:
            stmt = stmt.order_by(fb.updated_at.desc(), fb.id.desc())
        else:
            stmt = stmt.order_by(fb.next_attempt_at, fb.created_at, fb.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        with self._sa_engine.connect() as conn:
            return [AnnouncementFallback(**row) for row in conn.execute(stmt).mappings()]

    def list_announcement_fallback_attempts(
        self, fallback_ids: Iterable[str]
    ) -> list[AnnouncementFallbackAttempt]:
        ids = list(fallback_ids)
        if not ids:
            return []
        at = announcement_fallback_attempt.c
        stmt = (
            select(announcement_fallback_attempt)
            .where(at.fallback_id.in_(ids))
            .order_by(at.fallback_id, at.number)
        )
        with self._sa_engine.connect() as conn:
            return [AnnouncementFallbackAttempt(**row) for row in conn.execute(stmt).mappings()]

    def begin_announcement_fallback_attempt(self, fallback_id: str, attempt: dict) -> bool:
        """Commit send intent: pending -> sending with attempt ``number``.

        Succeeds only if the fallback is still pending, due, and has made
        exactly ``number - 1`` attempts; a lost race inserts nothing.
        """
        fb = announcement_fallback.c
        number = attempt["number"]
        acknowledged = exists().where(
            announcement_acknowledgement.c.occurrence_id == fb.occurrence_id
        )
        with self._sa_engine.begin() as conn:
            changed = conn.execute(
                update(announcement_fallback)
                .where(
                    fb.id == fallback_id,
                    fb.status == "pending",
                    fb.attempts_made == number - 1,
                    fb.attempts_made < fb.max_attempts,
                    ~acknowledged,  # Done/Skip committed first: never send
                )
                .values(
                    status="sending",
                    attempts_made=number,
                    next_attempt_at=None,
                    updated_at=attempt["intent_at"],
                )
            ).rowcount
            if changed != 1:
                return False
            conn.execute(
                insert(announcement_fallback_attempt).values(
                    fallback_id=fallback_id, status="sending", **attempt
                )
            )
        return True

    def finish_announcement_fallback_attempt(
        self, fallback_id: str, attempt_id: str, attempt_values: dict, **fallback_values
    ) -> bool:
        """Record a request outcome and the fallback's next state together."""
        at = announcement_fallback_attempt.c
        fb = announcement_fallback.c
        with self._sa_engine.begin() as conn:
            changed = conn.execute(
                update(announcement_fallback_attempt)
                .where(at.id == attempt_id, at.status == "sending")
                .values(**attempt_values)
            ).rowcount
            if changed != 1:
                return False
            conn.execute(
                update(announcement_fallback)
                .where(fb.id == fallback_id, fb.status == "sending")
                .values(**fallback_values)
            )
        return True

    def cancel_acknowledged_announcement_fallbacks(self, now: str) -> int:
        """Cancel pending fallbacks of occurrences the owner marked Done/Skip.

        Catches a fallback opened just after the acknowledgement committed;
        the acknowledgement itself cancels any that already existed.
        """
        fb = announcement_fallback.c
        acknowledged = exists().where(
            announcement_acknowledgement.c.occurrence_id == fb.occurrence_id
        )
        with self._sa_engine.begin() as conn:
            return conn.execute(
                update(announcement_fallback)
                .where(fb.status == "pending", acknowledged)
                .values(
                    status="cancelled",
                    status_reason="acknowledged",
                    next_attempt_at=None,
                    completed_at=now,
                    updated_at=now,
                )
            ).rowcount

    def recover_announcement_fallbacks(self, now: str) -> int:
        """Requests interrupted by a restart: acceptance unknown, never resent."""
        at = announcement_fallback_attempt.c
        fb = announcement_fallback.c
        with self._sa_engine.begin() as conn:
            conn.execute(
                update(announcement_fallback_attempt)
                .where(at.status == "sending")
                .values(status="unknown", detail="interrupted_by_restart", finished_at=now)
            )
            return conn.execute(
                update(announcement_fallback)
                .where(fb.status == "sending")
                .values(
                    status="unknown",
                    status_reason="interrupted_by_restart",
                    completed_at=now,
                    updated_at=now,
                )
            ).rowcount
