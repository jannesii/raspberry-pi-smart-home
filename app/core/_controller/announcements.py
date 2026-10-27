"""Speaker tests and announcement delivery attempts; no Cast or synthesis calls.

Status changes use conditional updates (``expected``) so a browser mutation and
the service loop cannot both win a transition (e.g. stop versus dispatch).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from ..models import AnnouncementAttempt, AnnouncementSpeakerTest
from ..schema import announcement_attempt, announcement_speaker_test

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

_TEST_FIELDS = ("text", "language", "target_key", "volume_percent", "chime_percent")


def _attempt(row) -> AnnouncementAttempt:
    values = dict(row)
    values["speaker_set"] = json.loads(values["speaker_set"])
    evidence = values.get("member_evidence")
    values["member_evidence"] = json.loads(evidence) if evidence else None
    return AnnouncementAttempt(**values)


def encode_attempt(values: dict) -> dict:
    """JSON-encode list columns of an attempt insert/update."""
    row = dict(values)
    if "speaker_set" in row:
        row["speaker_set"] = json.dumps(sorted(row["speaker_set"]))
    if row.get("member_evidence") is not None:
        row["member_evidence"] = json.dumps(row["member_evidence"])
    return row


class AnnouncementMixin:
    _sa_engine: Engine

    def create_announcement_speaker_test(
        self, test: dict, attempt: dict
    ) -> tuple[str, AnnouncementSpeakerTest]:
        """Insert a test and its attempt atomically, idempotent on ``request_id``.

        Returns ("created" | "exists" | "conflict", test).
        """
        row = encode_attempt(attempt)
        try:
            with self._sa_engine.begin() as conn:
                conn.execute(insert(announcement_speaker_test).values(**test))
                conn.execute(insert(announcement_attempt).values(**row))
        except IntegrityError:
            existing = self.get_announcement_speaker_test_by_request(test["request_id"])
            if existing is None:
                raise
            same = all(getattr(existing, f) == test[f] for f in _TEST_FIELDS)
            logger.debug("Repeated speaker test request same_payload=%s", same)
            return ("exists" if same else "conflict"), existing
        return "created", AnnouncementSpeakerTest(**test)

    def get_announcement_speaker_test(self, test_id: str) -> AnnouncementSpeakerTest | None:
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(
                    select(announcement_speaker_test).where(
                        announcement_speaker_test.c.id == test_id
                    )
                )
                .mappings()
                .first()
            )
        return AnnouncementSpeakerTest(**row) if row else None

    def get_announcement_speaker_test_by_request(
        self, request_id: str
    ) -> AnnouncementSpeakerTest | None:
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(
                    select(announcement_speaker_test).where(
                        announcement_speaker_test.c.request_id == request_id
                    )
                )
                .mappings()
                .first()
            )
        return AnnouncementSpeakerTest(**row) if row else None

    def list_announcement_speaker_tests(self, limit: int = 20) -> list[AnnouncementSpeakerTest]:
        stmt = (
            select(announcement_speaker_test)
            .order_by(
                announcement_speaker_test.c.requested_at.desc(),
                announcement_speaker_test.c.id.desc(),
            )
            .limit(limit)
        )
        with self._sa_engine.connect() as conn:
            return [AnnouncementSpeakerTest(**row) for row in conn.execute(stmt).mappings()]

    def get_announcement_attempt(self, attempt_id: str) -> AnnouncementAttempt | None:
        with self._sa_engine.connect() as conn:
            row = (
                conn.execute(
                    select(announcement_attempt).where(announcement_attempt.c.id == attempt_id)
                )
                .mappings()
                .first()
            )
        return _attempt(row) if row else None

    def list_announcement_attempts(
        self,
        *,
        statuses: Iterable[str] | None = None,
        speaker_test_ids: Iterable[str] | None = None,
        occurrence_ids: Iterable[str] | None = None,
    ) -> list[AnnouncementAttempt]:
        stmt = select(announcement_attempt)
        if statuses is not None:
            stmt = stmt.where(announcement_attempt.c.status.in_(list(statuses)))
        if speaker_test_ids is not None:
            stmt = stmt.where(announcement_attempt.c.speaker_test_id.in_(list(speaker_test_ids)))
        if occurrence_ids is not None:
            stmt = stmt.where(announcement_attempt.c.occurrence_id.in_(list(occurrence_ids)))
        stmt = stmt.order_by(
            announcement_attempt.c.priority.desc(),
            announcement_attempt.c.requested_at,
            announcement_attempt.c.id,
        )
        with self._sa_engine.connect() as conn:
            return [_attempt(row) for row in conn.execute(stmt).mappings()]

    def update_announcement_attempt(
        self,
        attempt_id: str,
        *,
        expected: Iterable[str] | None = None,
        require_no_stop: bool = False,
        **values,
    ) -> bool:
        """Apply ``values`` only while the attempt is in an ``expected`` status."""
        values.setdefault("updated_at", datetime.now(UTC).isoformat())
        values = encode_attempt(values)
        stmt = update(announcement_attempt).where(announcement_attempt.c.id == attempt_id)
        if expected is not None:
            stmt = stmt.where(announcement_attempt.c.status.in_(list(expected)))
        if require_no_stop:
            stmt = stmt.where(announcement_attempt.c.stop_requested_at.is_(None))
        with self._sa_engine.begin() as conn:
            changed = conn.execute(stmt.values(**values)).rowcount == 1
        if changed and "status" in values:
            logger.debug(
                "Announcement attempt %s -> %s reason=%s",
                attempt_id,
                values["status"],
                values.get("reason"),
            )
        return changed
