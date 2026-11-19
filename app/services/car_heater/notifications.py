"""Immediate, durable problem alerts; network I/O never holds a heater lock."""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ...environment import is_development
from ..alert_webhook import deliver_webhook

logger = logging.getLogger(__name__)
HELSINKI = ZoneInfo("Europe/Helsinki")


def iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def queue_problem(ctrl, session_id, key, message, *, now, finish=None):
    """Persist once per event; alert-storage failure must not block heater safety."""
    if ctrl is None or not hasattr(ctrl, "queue_heater_notification"):
        return False
    if finish is not None:
        end = datetime.fromtimestamp(finish, HELSINKI).strftime("%d.%m.%Y %H:%M:%S %Z")
        message += f"\nOriginal heating finish: {end}."
    occurred = datetime.fromtimestamp(now, HELSINKI).strftime("%d.%m.%Y %H:%M:%S %Z")
    message += f"\nDetected: {occurred}.\nSession: {session_id}"
    try:
        ctrl.queue_heater_notification(f"{session_id}:{key}", message, iso(now))
        return True
    except Exception:
        logger.exception("Could not persist car-heater notification event=%s", key)
        return False


class HeaterNotificationService:
    def __init__(self, ctrl, *, clock=time.time):
        self._ctrl = ctrl
        self._clock = clock
        self._stop = threading.Event()

    def start(self):
        if is_development():
            return
        self._ctrl.recover_heater_notifications(iso(self._clock()))
        self._thread = threading.Thread(target=self._run, name="HeaterNotifications", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def tick(self):
        if is_development():
            return
        now = self._clock()
        notice = self._ctrl.claim_heater_notification(iso(now))
        if not notice:
            return
        recovery = notice["event_key"].rsplit(":", 1)[-1] in (
            "recovery",
            "departure_start_recovery",
        )
        prefix = "" if recovery else "@everyone\n"
        result = deliver_webhook(
            payload={
                "content": prefix + "**Car heater alert**\n" + notice["message"],
                "allowed_mentions": {"parse": [] if recovery else ["everyone"]},
            },
            timeout=5,
        )
        status = result.status
        retry_at = None
        if status == "not_configured" or (
            status in ("failed", "rate_limited") and notice["attempts"] < 3
        ):
            status = "pending"
            delay = max(60, result.retry_after_s or 0)
            retry_at = (
                datetime.fromtimestamp(self._clock(), UTC) + timedelta(seconds=delay)
            ).isoformat()
        self._ctrl.finish_heater_notification(
            notice["event_key"], status, retry_at, attempted=result.status != "not_configured"
        )
        logger.info(
            "Car-heater notification event=%s outcome=%s", notice["event_key"], result.status
        )

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Car-heater notification delivery failed")
            self._stop.wait(2)
