"""Interruption events through real heater/planner state and durable delivery."""

from datetime import datetime

import pytest
import test_bounded_heating as bounded_tests
import test_car_heater_sessions as evidence
import test_departure_heating as departure_tests
from sqlalchemy import select

from app.core.schema import car_heater_notification as notices
from app.services.alert_webhook import WebhookResult
from app.services.car_heater import notifications
from app.services.car_heater.car_heater_service import CarHeaterService

session_app = evidence.session_app
service = bounded_tests.service
departure = departure_tests.departure
ready = bounded_tests.ready


def rows(ctrl):
    with ctrl._sa_engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(notices)).mappings()]


def observe(heater, run, relay=True, power=1200, phase=None):
    heater.observe_bounded_status(
        {
            "bounded": {
                "session_id": run["session_id"],
                "protocol": 1,
                "ready": True,
                "relay_on": relay,
                "power_w": power,
                "phase": phase or ("heating" if relay else "stopped"),
                "boot_id": "relay",
                "device_boot_id": "esp",
            }
        }
    )


@pytest.fixture
def running(service):
    heater, clock = service
    heater.stop()
    heater._thread.join(timeout=2)
    ready(heater)
    run = heater.start_bounded_session(60)
    observe(heater, run)
    return heater, clock, run


def test_restart_alert_survives_repeated_restart_without_on_replay(running, session_app):
    _heater, clock, run = running
    for _ in range(2):
        restarted = CarHeaterService(session_app.ctrl, clock=lambda: clock[0])
        restarted.stop()
        restarted._thread.join(timeout=2)
        assert restarted.get_bounded_session()["finish_at"] == run["finish_at"]
        assert all(c["action"] == "turn_off" for c in restarted.get_queued_commands())
    messages = rows(session_app.ctrl)
    assert len(messages) == 1
    assert "server restart" in messages[0]["message"]
    assert "not yet confirmed" in messages[0]["message"]
    assert run["session_id"] in messages[0]["message"]


def test_status_loss_has_grace_and_one_recovery_per_episode(running, session_app):
    heater, clock, run = running
    clock[0] += 59
    heater.tick_bounded()
    assert rows(session_app.ctrl) == []
    clock[0] += 1
    for _ in range(3):
        heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 1
    assert "unknown" in rows(session_app.ctrl)[0]["message"]
    observe(heater, run)
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 2
    assert any("available again" in row["message"] for row in rows(session_app.ctrl))
    clock[0] += 61
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 3


def test_malformed_relay_reports_do_not_hide_loss_of_known_status(running, session_app):
    heater, clock, run = running
    clock[0] += 61
    observe(heater, run, relay=None)
    heater.tick_bounded()
    assert "status_lost" in rows(session_app.ctrl)[0]["event_key"]


def test_unexpected_early_off_alerts_but_user_stop_and_normal_finish_do_not(running, session_app):
    heater, clock, run = running
    observe(heater, run, relay=False, power=0)
    assert len(rows(session_app.ctrl)) == 1
    assert "before its original finish" in rows(session_app.ctrl)[0]["message"]
    ready(heater)
    run = heater.start_bounded_session(60)
    observe(heater, run)
    heater.stop_bounded_session()
    observe(heater, run, relay=False, power=0)
    assert len(rows(session_app.ctrl)) == 1
    ready(heater)
    run = heater.start_bounded_session(60)
    observe(heater, run)
    clock[0] = run["finish_at"]
    heater.tick_bounded()
    observe(heater, run, relay=False, power=0)
    assert len(rows(session_app.ctrl)) == 1


def test_no_power_requires_sustained_fresh_reports_then_recovers(running, session_app):
    heater, clock, run = running
    for elapsed in (0, 30, 60, 65):
        clock[0] = run["issued_at"] + elapsed
        observe(heater, run, power=0)
        heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 1
    assert "heating power" in rows(session_app.ctrl)[0]["message"]
    observe(heater, run, power=1200)
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 2


def test_unconfirmed_start_and_off_warn_without_changing_deadline(service, session_app):
    heater, clock = service
    heater.stop()
    heater._thread.join(timeout=2)
    ready(heater)
    run = heater.start_bounded_session()
    clock[0] += 30
    heater.tick_bounded()
    assert "start_unconfirmed" in rows(session_app.ctrl)[0]["event_key"]
    clock[0] += 30
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 2
    assert heater.get_bounded_session()["finish_at"] == run["finish_at"]


def test_failed_command_alerts_and_storage_failure_never_blocks_off(
    running, session_app, monkeypatch
):
    heater, _clock, run = running
    heater.observe_bounded_result(
        {"session_id": run["session_id"], "action": "turn_on", "success": False}
    )
    assert "execution_failed" in rows(session_app.ctrl)[0]["event_key"]
    monkeypatch.setattr(
        session_app.ctrl,
        "queue_heater_notification",
        lambda *a: (_ for _ in ()).throw(OSError("storage unavailable")),
    )
    heater.observe_bounded_result(
        {"session_id": run["session_id"], "action": "turn_off", "success": False}
    )
    assert heater.get_bounded_session()["off_state"] == "failed_or_unknown"
    assert all(c["action"] == "turn_off" for c in heater.get_queued_commands())


@pytest.mark.parametrize("outcome", ["accepted", "unknown", "rejected"])
def test_delivery_deduplicates_and_never_retries_ambiguous_outcome(
    session_app, monkeypatch, outcome
):
    ctrl = session_app.ctrl
    ctrl.queue_heater_notification("session:restart", "Heating interrupted", notifications.iso(100))
    assert (
        ctrl.queue_heater_notification("session:restart", "duplicate", notifications.iso(100))
        is False
    )
    calls = []
    monkeypatch.setattr(
        notifications, "deliver_webhook", lambda **kw: (calls.append(kw) or WebhookResult(outcome))
    )
    sender = notifications.HeaterNotificationService(ctrl, clock=lambda: 100)
    sender.tick()
    sender.tick()
    assert len(calls) == 1
    assert rows(ctrl)[0]["status"] == outcome
    assert calls[0]["payload"]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[0]["payload"]["content"].startswith("@everyone\n")


def test_certain_failure_and_rate_limit_retry_with_backoff(session_app, monkeypatch):
    ctrl = session_app.ctrl
    clock = [100]
    ctrl.queue_heater_notification("retry", "Interrupted", notifications.iso(100))
    calls = []
    outcomes = iter(
        [
            WebhookResult("rate_limited", retry_after_s=120),
            WebhookResult("failed"),
            WebhookResult("accepted"),
        ]
    )
    monkeypatch.setattr(
        notifications, "deliver_webhook", lambda **kw: (calls.append(kw) or next(outcomes))
    )
    sender = notifications.HeaterNotificationService(ctrl, clock=lambda: clock[0])
    sender.tick()
    clock[0] = 219
    sender.tick()
    assert len(calls) == 1
    clock[0] = 220
    sender.tick()
    clock[0] = 280
    sender.tick()
    assert len(calls) == 3
    assert rows(ctrl)[0]["status"] == "accepted"


def test_inflight_request_after_restart_is_unknown_not_resent(session_app, monkeypatch):
    ctrl = session_app.ctrl
    ctrl.queue_heater_notification("crashed", "Interrupted", notifications.iso(100))
    assert ctrl.claim_heater_notification(notifications.iso(100))
    ctrl.recover_heater_notifications(notifications.iso(101))
    monkeypatch.setattr(
        notifications, "deliver_webhook", lambda **kw: pytest.fail("must not resend")
    )
    notifications.HeaterNotificationService(ctrl, clock=lambda: 102).tick()
    assert rows(ctrl)[0]["status"] == "unknown"


def test_development_never_posts(session_app, monkeypatch):
    monkeypatch.setattr(notifications, "is_development", lambda: True)
    monkeypatch.setattr(notifications, "deliver_webhook", lambda **kw: pytest.fail("live webhook"))
    ctrl = session_app.ctrl
    ctrl.queue_heater_notification("dev", "Interrupted", notifications.iso(100))
    sender = notifications.HeaterNotificationService(ctrl, clock=lambda: 100)
    sender.start()
    sender.tick()
    assert rows(ctrl)[0]["status"] == "pending"


def test_missed_departure_alerts_once_and_expiry_is_explicit(departure, session_app):
    planner, _heater, clock, _weather = departure
    plan = planner.save("2027-01-16T08:00")
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    planner.tick()
    assert rows(session_app.ctrl) == []
    clock[0] += 61
    planner.tick()
    planner.tick()
    assert len(rows(session_app.ctrl)) == 1
    assert "unable to start" in rows(session_app.ctrl)[0]["message"]
    clock[0] = datetime.fromisoformat(plan["finish_at"]).timestamp()
    planner.tick()
    assert len(rows(session_app.ctrl)) == 2


def test_device_timer_early_margin_is_not_an_interruption(running, session_app):
    heater, clock, run = running
    clock[0] = run["finish_at"] - 3
    observe(heater, run, relay=False, power=0)
    assert rows(session_app.ctrl) == []


def test_off_confirmation_after_warning_sends_one_followup(running, session_app):
    heater, clock, run = running
    heater.stop_bounded_session()
    clock[0] += 30
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 1
    observe(heater, run, relay=False, power=0)
    observe(heater, run, relay=False, power=0)
    messages = rows(session_app.ctrl)
    assert len(messages) == 2
    assert any("OFF is now confirmed" in row["message"] for row in messages)


def test_missing_power_evidence_warns_without_inventing_a_power_reading(running, session_app):
    heater, clock, run = running
    for elapsed in (0, 30, 60):
        clock[0] = run["issued_at"] + elapsed
        observe(heater, run, power=None)
        heater.tick_bounded()
    messages = rows(session_app.ctrl)
    assert len(messages) == 1
    assert "missing measurements" in messages[0]["message"]
    assert heater.get_bounded_session()["power_w"] is None


def test_offline_notification_storage_retries_the_episode(running, session_app, monkeypatch):
    heater, clock, _run = running
    queue = session_app.ctrl.queue_heater_notification
    monkeypatch.setattr(
        session_app.ctrl,
        "queue_heater_notification",
        lambda *a: (_ for _ in ()).throw(OSError("offline")),
    )
    clock[0] += 61
    heater.tick_bounded()
    assert rows(session_app.ctrl) == []
    monkeypatch.setattr(session_app.ctrl, "queue_heater_notification", queue)
    heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 1


def test_missing_webhook_leaves_alert_pending(session_app, monkeypatch):
    ctrl = session_app.ctrl
    clock = [100]
    ctrl.queue_heater_notification("unconfigured", "Interrupted", notifications.iso(100))
    monkeypatch.setattr(
        notifications, "deliver_webhook", lambda **kw: WebhookResult("not_configured")
    )
    sender = notifications.HeaterNotificationService(ctrl, clock=lambda: clock[0])
    sender.tick()
    assert rows(ctrl)[0]["status"] == "pending"
    monkeypatch.setattr(notifications, "deliver_webhook", lambda **kw: WebhookResult("accepted"))
    clock[0] += 60
    sender.tick()
    assert rows(ctrl)[0]["status"] == "accepted"


def test_delayed_departure_recovery_requires_confirmed_relay_on(departure, session_app):
    planner, heater, clock, _weather = departure
    plan = planner.save("2027-01-16T08:00")
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp()
    planner.tick()
    clock[0] += 61
    planner.tick()
    ready(heater)
    planner.tick()
    assert len(rows(session_app.ctrl)) == 1  # ON request alone is not recovery.
    observe(heater, heater.get_bounded_session())
    planner.tick()
    planner.tick()
    assert len(rows(session_app.ctrl)) == 2
    assert any("ON is now confirmed" in row["message"] for row in rows(session_app.ctrl))


@pytest.mark.parametrize(
    "event", ["session:status_lost:1:recovery", "session:departure_start_recovery"]
)
def test_recovery_mentions_are_disabled(session_app, monkeypatch, event):
    ctrl = session_app.ctrl
    ctrl.queue_heater_notification(event, "Status recovered", notifications.iso(100))
    calls = []
    monkeypatch.setattr(
        notifications,
        "deliver_webhook",
        lambda **kw: (calls.append(kw) or WebhookResult("accepted")),
    )
    notifications.HeaterNotificationService(ctrl, clock=lambda: 100).tick()
    payload = calls[0]["payload"]
    assert payload["allowed_mentions"] == {"parse": []}
    assert "@everyone" not in payload["content"]


def test_first_late_departure_tick_with_ready_device_still_warns(departure, session_app):
    planner, heater, clock, _weather = departure
    plan = planner.save("2027-01-16T08:00")
    clock[0] = datetime.fromisoformat(plan["planned_start_at"]).timestamp() + 61
    ready(heater)
    planner.tick()
    messages = rows(session_app.ctrl)
    assert len(messages) == 1
    assert messages[0]["event_key"].endswith(":departure_delayed")
    run = heater.get_bounded_session()
    assert run["phase"] == "starting"
    observe(heater, run)
    planner.tick()
    planner.tick()
    assert len(rows(session_app.ctrl)) == 2
    assert any(r["event_key"].endswith(":departure_start_recovery") for r in rows(session_app.ctrl))
    assert (
        heater.get_bounded_session()["finish_at"]
        == datetime.fromisoformat(plan["finish_at"]).timestamp()
    )


def test_status_gap_does_not_falsely_recover_a_missing_power_episode(running, session_app):
    heater, clock, run = running
    for elapsed in (0, 30, 60):
        clock[0] = run["issued_at"] + elapsed
        observe(heater, run, power=0)
        heater.tick_bounded()
    assert len(rows(session_app.ctrl)) == 1
    clock[0] += 61
    heater.tick_bounded()
    observe(heater, run, power=0)
    heater.tick_bounded()
    no_power = [r for r in rows(session_app.ctrl) if ":no_power:" in r["event_key"]]
    assert len(no_power) == 1
    assert no_power[0]["event_key"].endswith(":problem")
    observe(heater, run, power=1200)
    heater.tick_bounded()
    no_power = [r for r in rows(session_app.ctrl) if ":no_power:" in r["event_key"]]
    assert len(no_power) == 2
    assert any(r["event_key"].endswith(":recovery") for r in no_power)
