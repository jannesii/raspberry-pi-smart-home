"""Transactional removal of terminal evidence, retaining only consumed identities."""

from sqlalchemy import delete, insert, select

from ..schema import (
    announcement_acknowledgement as ack,
    announcement_attempt as attempt,
    announcement_consumed as consumed,
    announcement_fallback as fallback,
    announcement_fallback_attempt as fallback_attempt,
    announcement_occurrence as occurrence,
    announcement_schedule as schedule,
    announcement_snooze as snooze,
    announcement_speaker_test as speaker_test,
)


class AnnouncementHistoryMixin:
    def announcement_request_consumed(self, request_id):
        with self._sa_engine.connect() as conn:
            return (
                conn.execute(
                    select(consumed.c.id).where(consumed.c.request_id == request_id)
                ).first()
                is not None
            )

    def delete_announcement_history(self, kind, identity):
        """Return deleted/missing/active; never remove pending work or retry budgets.

        The service serializes this with dispatch and restoration. Schedule then
        occurrence locking matches Snooze/materialization in PostgreSQL.
        """
        table = occurrence if kind == "occurrences" else speaker_test
        with self._sa_engine.begin() as conn:
            if kind == "occurrences":
                schedule_id = conn.execute(
                    select(table.c.schedule_id).where(table.c.id == identity)
                ).scalar_one_or_none()
                if schedule_id:
                    conn.execute(
                        select(schedule.c.id).where(schedule.c.id == schedule_id).with_for_update()
                    ).first()
            row = (
                conn.execute(select(table).where(table.c.id == identity).with_for_update())
                .mappings()
                .first()
            )
            if row is None:
                return "missing"
            link = attempt.c.occurrence_id if kind == "occurrences" else attempt.c.speaker_test_id
            attempts = (
                conn.execute(select(attempt).where(link == identity).with_for_update())
                .mappings()
                .all()
            )
            if any(
                a["status"] in ("requested", "dispatching", "dispatched", "playing")
                or a["media_restore"] == "pending"
                or a["volume_restore"] == "pending"
                or (a["volume_applied"] is not None and a["volume_restore"] is None)
                for a in attempts
            ):
                return "active"
            tombstone = {
                "id": identity,
                "request_id": None,
                "schedule_id": None,
                "instance_due_at": None,
            }
            if kind == "occurrences":
                fb = (
                    conn.execute(
                        select(fallback)
                        .where(fallback.c.occurrence_id == identity)
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if (
                    row["status"] in ("waiting", "announcing")
                    or (fb and fb["status"] in ("pending", "sending"))
                    or (row["fallback_reason"] and fb is None)
                ):
                    return "active"
                tombstone.update(
                    schedule_id=row["schedule_id"], instance_due_at=row["instance_due_at"]
                )
                if fb:
                    conn.execute(
                        delete(fallback_attempt).where(fallback_attempt.c.fallback_id == fb["id"])
                    )
                    conn.execute(delete(fallback).where(fallback.c.id == fb["id"]))
                conn.execute(delete(ack).where(ack.c.occurrence_id == identity))
                conn.execute(delete(snooze).where(snooze.c.occurrence_id == identity))
            else:
                tombstone["request_id"] = row["request_id"]
            conn.execute(insert(consumed).values(**tombstone))
            conn.execute(delete(attempt).where(link == identity))
            conn.execute(delete(table).where(table.c.id == identity))
        return "deleted"
