from datetime import datetime, time, timedelta

from app.core.models import MedicinePurchase, MedicineRefillCalculation
from app.services.medicine import medicine_alert_service
from app.services.medicine.medicine_alert_service import MedicineAlertService


class _WaitEvent:
    def __init__(self, *, stop_after_iterations: int | None = None) -> None:
        self.stop_after_iterations = stop_after_iterations
        self.is_set_calls = 0
        self.wait_calls: list[float] = []

    def is_set(self) -> bool:
        self.is_set_calls += 1
        return (
            self.stop_after_iterations is not None
            and self.is_set_calls > self.stop_after_iterations
        )

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return False


class _ImmediateThread:
    def __init__(self, *, target, **kwargs) -> None:
        del kwargs
        self.target = target

    def start(self) -> None:
        self.target()


def test_send_alert_webhook_retries_with_exponential_backoff(monkeypatch):
    service = MedicineAlertService(ctrl=object())
    stop_event = _WaitEvent()
    service._routine_stop = stop_event
    results = iter([False, False, True])
    calls: list[tuple[str, str]] = []

    def fake_send_alert_webhook(*, title: str, message: str) -> bool:
        calls.append((title, message))
        return next(results)

    monkeypatch.setattr(medicine_alert_service, "send_alert_webhook", fake_send_alert_webhook)

    succeeded = service._send_alert_webhook(
        title="Medicine Alert",
        message="Test message",
        max_attempts=3,
        initial_delay_seconds=0.25,
        backoff_factor=3,
    )

    assert succeeded is True
    assert calls == [("Medicine Alert", "Test message")] * 3
    assert stop_event.wait_calls == [0.25, 0.75]


def test_send_alert_webhook_returns_false_after_attempt_limit(monkeypatch):
    service = MedicineAlertService(ctrl=object())
    stop_event = _WaitEvent()
    service._routine_stop = stop_event
    calls = 0

    def fake_send_alert_webhook(*, title: str, message: str) -> bool:
        nonlocal calls
        del title, message
        calls += 1
        return False

    monkeypatch.setattr(medicine_alert_service, "send_alert_webhook", fake_send_alert_webhook)

    succeeded = service._send_alert_webhook(
        title="Medicine Alert",
        message="Test message",
        max_attempts=2,
        initial_delay_seconds=0.5,
    )

    assert succeeded is False
    assert calls == 2
    assert stop_event.wait_calls == [0.5]


def _eligible_calculation(next_purchase_date: str = "2026-06-12"):
    return MedicineRefillCalculation(
        purchase_date="2026-05-01",
        run_out_date="2026-06-19",
        next_purchase_date=next_purchase_date,
        flex_days=7,
        treatment_days=30,
        dosing_days_covered=30,
        dosing_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )


def test_daily_alert_uses_cooldown_and_stops_after_batch_limit(monkeypatch):
    purchase = MedicinePurchase(id=1, medicine_name="Medicine A")

    class ControllerStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_latest_medicine_purchases(self):
            self.list_calls += 1
            return [purchase]

        def calculate_medicine_purchase(self, selected_purchase):
            assert selected_purchase is purchase
            return _eligible_calculation()

    ctrl = ControllerStub()
    service = MedicineAlertService(ctrl)
    send_calls = 0

    def failed_send(**kwargs) -> bool:
        nonlocal send_calls
        del kwargs
        send_calls += 1
        return False

    monkeypatch.setattr(service, "_send_alert_webhook", failed_send)
    noon = service.tz.localize(datetime(2026, 6, 12, 12, 0))

    service._process_daily_alert(
        noon,
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=2,
    )
    service._process_daily_alert(
        noon + timedelta(minutes=5),
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=2,
    )
    service._process_daily_alert(
        noon + timedelta(minutes=15),
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=2,
    )
    service._process_daily_alert(
        noon + timedelta(hours=1),
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=2,
    )

    assert send_calls == 2
    assert ctrl.list_calls == 2
    assert service._exhausted_date == noon.date()


def test_daily_alert_resets_retry_limit_on_next_date(monkeypatch):
    purchase = MedicinePurchase(id=1, medicine_name="Medicine A")

    class ControllerStub:
        next_purchase_date = "2026-06-12"

        def list_latest_medicine_purchases(self):
            return [purchase]

        def calculate_medicine_purchase(self, selected_purchase):
            assert selected_purchase is purchase
            return _eligible_calculation(self.next_purchase_date)

    ctrl = ControllerStub()
    service = MedicineAlertService(ctrl)
    send_results = iter([False, True])
    send_calls = 0

    def send_with_results(**kwargs) -> bool:
        nonlocal send_calls
        del kwargs
        send_calls += 1
        return next(send_results)

    monkeypatch.setattr(service, "_send_alert_webhook", send_with_results)
    first_noon = service.tz.localize(datetime(2026, 6, 12, 12, 0))
    service._process_daily_alert(
        first_noon,
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=1,
    )

    ctrl.next_purchase_date = "2026-06-13"
    service._process_daily_alert(
        first_noon + timedelta(days=1),
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=1,
    )

    assert send_calls == 2
    assert service._exhausted_date == first_noon.date()
    assert service._completed_date == (first_noon + timedelta(days=1)).date()


def test_daily_alert_marks_successful_date_complete(monkeypatch):
    purchase = MedicinePurchase(id=1, medicine_name="Medicine A")

    class ControllerStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_latest_medicine_purchases(self):
            self.list_calls += 1
            return [purchase]

        def calculate_medicine_purchase(self, selected_purchase):
            assert selected_purchase is purchase
            return _eligible_calculation()

    ctrl = ControllerStub()
    service = MedicineAlertService(ctrl)
    sent_messages: list[str] = []

    def successful_send(*, message: str, **kwargs) -> bool:
        del kwargs
        sent_messages.append(message)
        return True

    monkeypatch.setattr(service, "_send_alert_webhook", successful_send)
    noon = service.tz.localize(datetime(2026, 6, 12, 12, 0))

    for offset_minutes in (0, 5, 60):
        service._process_daily_alert(
            noon + timedelta(minutes=offset_minutes),
            trigger_time=time(12, 0),
            retry_interval_seconds=900,
            max_daily_delivery_batches=3,
        )

    assert ctrl.list_calls == 1
    assert sent_messages == [
        "These medicines are available for purchase today:\n- Medicine A\n\n @everyone"
    ]
    assert service._completed_date == noon.date()


def test_daily_alert_marks_no_eligible_purchase_date_complete(monkeypatch):
    purchase = MedicinePurchase(id=1, medicine_name="Medicine A")

    class ControllerStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_latest_medicine_purchases(self):
            self.list_calls += 1
            return [purchase]

        def calculate_medicine_purchase(self, selected_purchase):
            assert selected_purchase is purchase
            return _eligible_calculation(next_purchase_date="2026-06-13")

    ctrl = ControllerStub()
    service = MedicineAlertService(ctrl)
    send_calls = 0

    def unexpected_send(**kwargs) -> bool:
        nonlocal send_calls
        del kwargs
        send_calls += 1
        return True

    monkeypatch.setattr(service, "_send_alert_webhook", unexpected_send)
    noon = service.tz.localize(datetime(2026, 6, 12, 12, 0))

    service._process_daily_alert(
        noon,
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=3,
    )
    service._process_daily_alert(
        noon + timedelta(hours=1),
        trigger_time=time(12, 0),
        retry_interval_seconds=900,
        max_daily_delivery_batches=3,
    )

    assert ctrl.list_calls == 1
    assert send_calls == 0
    assert service._completed_date == noon.date()


def test_routine_recovers_from_transient_controller_error(monkeypatch):
    purchase = MedicinePurchase(id=1, medicine_name="Medicine A")

    class ControllerStub:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_latest_medicine_purchases(self):
            self.list_calls += 1
            if self.list_calls == 1:
                raise RuntimeError("temporary database error")
            return [purchase]

        def calculate_medicine_purchase(self, selected_purchase):
            assert selected_purchase is purchase
            return _eligible_calculation()

    class FixedTimeMedicineAlertService(MedicineAlertService):
        @property
        def now_in_tz(self) -> datetime:
            return self.tz.localize(datetime(2026, 6, 12, 12, 0))

    ctrl = ControllerStub()
    service = FixedTimeMedicineAlertService(ctrl)
    stop_event = _WaitEvent(stop_after_iterations=2)
    sent_messages: list[str] = []

    def successful_send(*, message: str, **kwargs) -> bool:
        del kwargs
        sent_messages.append(message)
        return True

    monkeypatch.setattr(medicine_alert_service.threading, "Event", lambda: stop_event)
    monkeypatch.setattr(medicine_alert_service.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(service, "_send_alert_webhook", successful_send)

    service.start_routine(poll_seconds=1, trigger_time=time(12, 0))

    assert ctrl.list_calls == 2
    assert sent_messages == [
        "These medicines are available for purchase today:\n- Medicine A\n\n @everyone"
    ]
    assert stop_event.wait_calls == [1, 1]
