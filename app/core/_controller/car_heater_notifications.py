"""Durable deduplication and delivery claims for car-heater problem alerts."""

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from ..schema import car_heater_notification as notices


class CarHeaterNotificationMixin:
    def queue_heater_notification(self, event_key, message, now):
        try:
            with self._sa_engine.begin() as conn:
                conn.execute(
                    insert(notices).values(
                        event_key=event_key,
                        message=message,
                        created_at=now,
                        next_attempt_at=now,
                        status="pending",
                        attempts=0,
                    )
                )
        except IntegrityError:
            # A repeated tick or restart must not resend the same event.
            return False
        return True

    def recover_heater_notifications(self, now):
        # A crashed sender may already have reached Discord. Never blindly replay.
        with self._sa_engine.begin() as conn:
            conn.execute(
                update(notices).where(notices.c.status == "sending").values(status="unknown")
            )

    def claim_heater_notification(self, now):
        with self._sa_engine.begin() as conn:
            row = (
                conn.execute(
                    select(notices)
                    .where(notices.c.status == "pending", notices.c.next_attempt_at <= now)
                    .order_by(notices.c.created_at, notices.c.event_key)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            claimed = conn.execute(
                update(notices)
                .where(notices.c.event_key == row["event_key"], notices.c.status == "pending")
                .values(status="sending", attempts=notices.c.attempts + 1)
            )
            if not claimed.rowcount:
                return None
            return {**row, "attempts": row["attempts"] + 1}

    def finish_heater_notification(self, event_key, status, retry_at=None, *, attempted=True):
        values = {"status": status}
        if not attempted:
            values["attempts"] = notices.c.attempts - 1
        if retry_at is not None:
            values["next_attempt_at"] = retry_at
        with self._sa_engine.begin() as conn:
            conn.execute(
                update(notices)
                .where(notices.c.event_key == event_key, notices.c.status == "sending")
                .values(**values)
            )
