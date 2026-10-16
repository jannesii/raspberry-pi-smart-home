import logging
import threading
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytz

from ..alert_webhook import send_alert_webhook

if TYPE_CHECKING:
    from app.core.controller import Controller
    from app.core.models import MedicinePurchase, MedicineRefillCalculation

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MedicineAlertService:
    def __init__(self, ctrl) -> None:
        self.tz = pytz.timezone("Europe/Helsinki")
        self.ctrl: Controller = ctrl
        self._routine_stop = threading.Event()
        self._completed_date: date | None = None
        self._retry_date: date | None = None
        self._next_retry_at: datetime | None = None
        self._delivery_batches = 0
        self._exhausted_date: date | None = None

    @property
    def now_in_tz(self) -> datetime:
        return datetime.now(self.tz)

    def _send_alert_webhook(
        self,
        title: str,
        message: str,
        max_attempts: int = 5,
        initial_delay_seconds: float = 5.0,
        backoff_factor: float = 2.0,
    ) -> bool:
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info("Sending webhook attempt %s/%s", attempt, max_attempts)

                if send_alert_webhook(title=title, message=message):
                    logger.info("Webhook sent successfully")
                    return True

                logger.warning("Webhook returned False on attempt %s/%s", attempt, max_attempts)

            except Exception:
                logger.exception("Webhook failed on attempt %s/%s", attempt, max_attempts)

            if attempt < max_attempts:
                delay = initial_delay_seconds * (backoff_factor ** (attempt - 1))
                logger.info("Retrying webhook in %.1f seconds", delay)

                if self._routine_stop.wait(delay):
                    logger.info("Routine stopped while waiting to retry webhook")
                    return False

        logger.error("Webhook failed after %s attempts", max_attempts)
        return False

    def _process_daily_alert(
        self,
        now: datetime,
        *,
        trigger_time: time,
        retry_interval_seconds: int,
        max_daily_delivery_batches: int,
    ) -> None:
        today = now.date()
        current_time = now.time().replace(second=0, microsecond=0)
        if current_time < trigger_time:
            return
        if self._completed_date == today or self._exhausted_date == today:
            return

        if self._retry_date != today:
            logger.debug("Resetting medicine alert retry state for date=%s", today)
            self._retry_date = today
            self._next_retry_at = None
            self._delivery_batches = 0

        if self._next_retry_at is not None and now < self._next_retry_at:
            logger.debug("Medicine alert retry deferred until %s", self._next_retry_at.isoformat())
            return

        purchases: list[MedicinePurchase] = self.ctrl.list_latest_medicine_purchases()
        purchases_available_today: list[MedicinePurchase] = []
        for purchase in purchases:
            result: MedicineRefillCalculation = self.ctrl.calculate_medicine_purchase(purchase)
            next_purchase_date = date.fromisoformat(result.next_purchase_date)
            if next_purchase_date == today:
                purchases_available_today.append(purchase)
                logger.debug("Medicine is eligible for purchase name=%s", purchase.medicine_name)

        if not purchases_available_today:
            self._completed_date = today
            logger.debug("Medicine alert date completed with no eligible purchases date=%s", today)
            return

        message = "These medicines are available for purchase today:\n"
        message += "\n".join(f"- {p.medicine_name}" for p in purchases_available_today)
        message += "\n\n @everyone"
        self._delivery_batches += 1
        logger.info(
            "Sending medicine webhook batch %s/%s",
            self._delivery_batches,
            max_daily_delivery_batches,
        )
        webhook_succeeded = self._send_alert_webhook(
            title="Medicine Alert",
            message=message,
            max_attempts=5,
            initial_delay_seconds=5,
            backoff_factor=2,
        )
        if webhook_succeeded:
            self._completed_date = today
            self._next_retry_at = None
            logger.info("Medicine alert completed for date=%s", today)
            return

        if self._delivery_batches >= max_daily_delivery_batches:
            self._exhausted_date = today
            self._next_retry_at = None
            logger.error(
                "Medicine alert exhausted %s delivery batches for date=%s",
                max_daily_delivery_batches,
                today,
            )
            return

        self._next_retry_at = now + timedelta(seconds=retry_interval_seconds)
        logger.warning(
            "Medicine alert batch failed; next retry at %s",
            self._next_retry_at.isoformat(),
        )

    def start_routine(
        self,
        poll_seconds: int = 5,
        trigger_time: time = time(12, 0),
        retry_interval_seconds: int = 15 * 60,
        max_daily_delivery_batches: int = 3,
    ) -> None:
        logger.debug(
            (
                "start_routine called poll_seconds=%s trigger_time=%s "
                "retry_interval_seconds=%s max_daily_delivery_batches=%s"
            ),
            poll_seconds,
            trigger_time.isoformat(),
            retry_interval_seconds,
            max_daily_delivery_batches,
        )
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be positive")
        if max_daily_delivery_batches <= 0:
            raise ValueError("max_daily_delivery_batches must be positive")
        self._routine_stop = threading.Event()

        def runner() -> None:
            while not self._routine_stop.is_set():
                try:
                    self._process_daily_alert(
                        self.now_in_tz,
                        trigger_time=trigger_time,
                        retry_interval_seconds=retry_interval_seconds,
                        max_daily_delivery_batches=max_daily_delivery_batches,
                    )
                except Exception:
                    logger.exception("Medicine alert routine iteration failed")

                self._routine_stop.wait(poll_seconds)

        self._routine_thread = threading.Thread(target=runner, name="MedicineRoutine", daemon=True)
        self._routine_thread.start()
