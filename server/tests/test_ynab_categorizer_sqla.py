from __future__ import annotations

import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core import Controller, YnabApplyEvent, YnabBootstrapState
from app.core.schema import metadata
from app.core.sqlalchemy_engine import get_engine


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield f"{tmpdir}/test.db"


@pytest.fixture
def controller(temp_db):
    ctrl = Controller(db_path=temp_db)
    if ctrl._sa_engine is None:
        ctrl._sa_engine = get_engine(temp_db)
    metadata.create_all(ctrl._sa_engine)
    yield ctrl
    if ctrl._sa_engine:
        ctrl._sa_engine.dispose()


def test_config_defaults_to_strict(controller: Controller):
    cfg = controller.get_ynab_categorizer_config("budget1")
    assert cfg.queue_filter_mode == "strict"


def test_config_save_and_read(controller: Controller):
    saved = controller.save_ynab_categorizer_config("budget1", "skip_transfers")
    assert saved.queue_filter_mode == "skip_transfers"

    loaded = controller.get_ynab_categorizer_config("budget1")
    assert loaded.queue_filter_mode == "skip_transfers"


def test_increment_stats_upsert(controller: Controller):
    controller.increment_ynab_payee_category_stat(
        "budget1",
        "K MARKET",
        "cat_groceries",
        last_used_at="2026-03-09",
    )
    controller.increment_ynab_payee_category_stat(
        "budget1",
        "K MARKET",
        "cat_groceries",
        last_used_at="2026-03-10",
    )

    stats = controller.get_ynab_stats_for_payees("budget1", ["K MARKET"])
    assert "K MARKET" in stats
    assert len(stats["K MARKET"]) == 1
    assert stats["K MARKET"][0].count == 2
    assert stats["K MARKET"][0].last_used_at == "2026-03-10"


def test_apply_event_idempotency(controller: Controller):
    tz = ZoneInfo("Europe/Helsinki")
    applied_at = datetime.now(tz).isoformat()
    event = YnabApplyEvent(
        budget_id="budget1",
        transaction_id="tx1",
        payee_normalized="K MARKET",
        category_id="cat_groceries",
        applied_by_username="root",
        applied_at=applied_at,
    )

    first = controller.record_ynab_apply_event(event)
    second = controller.record_ynab_apply_event(event)

    assert first is True
    assert second is False
    assert controller.has_ynab_apply_event("budget1", "tx1") is True


def test_bootstrap_state_roundtrip(controller: Controller):
    state = YnabBootstrapState(
        budget_id="budget1",
        bootstrapped_at="2026-03-09T10:00:00+02:00",
        history_start_date="2024-03-09",
        history_end_date="2026-03-09",
    )
    controller.save_ynab_bootstrap_state(state)

    loaded = controller.get_ynab_bootstrap_state("budget1")
    assert loaded is not None
    assert loaded.budget_id == state.budget_id
    assert loaded.bootstrapped_at == state.bootstrapped_at
    assert loaded.history_start_date == state.history_start_date
    assert loaded.history_end_date == state.history_end_date
