from __future__ import annotations

import importlib
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, Text, create_engine, inspect

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
    assert cfg.show_reconciled_transactions is False
    assert cfg.queue_limit_enabled is False
    assert cfg.queue_limit_value == 30
    assert cfg.queue_limit_unit == "days"
    assert cfg.quick_apply_include_medium is False
    assert cfg.default_category_id is None
    assert cfg.custom_rules_json is None


def test_config_save_and_read(controller: Controller):
    saved = controller.save_ynab_categorizer_config(
        "budget1",
        "skip_transfers",
        show_reconciled_transactions=True,
        queue_limit_enabled=True,
        queue_limit_value=14,
        queue_limit_unit="days",
        quick_apply_include_medium=True,
        default_category_id="cat_transport",
        custom_rules_json='[{"id":"rule-1","enabled":true,"payee_match_type":"contains","payee_value":"abc","amount_operator":"any","amount_value_eur":null,"category_id":"cat_transport"}]',
    )
    assert saved.queue_filter_mode == "skip_transfers"
    assert saved.show_reconciled_transactions is True
    assert saved.queue_limit_enabled is True
    assert saved.queue_limit_value == 14
    assert saved.queue_limit_unit == "days"
    assert saved.quick_apply_include_medium is True
    assert saved.default_category_id == "cat_transport"
    assert saved.custom_rules_json is not None

    loaded = controller.get_ynab_categorizer_config("budget1")
    assert loaded.queue_filter_mode == "skip_transfers"
    assert loaded.show_reconciled_transactions is True
    assert loaded.queue_limit_enabled is True
    assert loaded.queue_limit_value == 14
    assert loaded.queue_limit_unit == "days"
    assert loaded.quick_apply_include_medium is True
    assert loaded.default_category_id == "cat_transport"
    assert loaded.custom_rules_json == saved.custom_rules_json


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


def test_category_usage_counts_aggregated(controller: Controller):
    controller.increment_ynab_payee_category_stat(
        "budget1",
        "K MARKET",
        "cat_groceries",
        last_used_at="2026-03-09",
    )
    controller.increment_ynab_payee_category_stat(
        "budget1",
        "K CITYMARKET",
        "cat_groceries",
        last_used_at="2026-03-10",
    )
    controller.increment_ynab_payee_category_stat(
        "budget1",
        "HSL",
        "cat_transport",
        last_used_at="2026-03-10",
    )

    usage = controller.get_ynab_category_usage_counts("budget1")
    assert usage["cat_groceries"] == 2
    assert usage["cat_transport"] == 1


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


def test_alembic_upgrade_adds_custom_rules_json_column(tmp_path):
    db_path = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    old_metadata = MetaData()
    Table(
        "ynab_categorizer_config",
        old_metadata,
        Column("id", Integer, primary_key=True),
        Column("budget_id", Text, nullable=False),
        Column("queue_filter_mode", Text, nullable=False),
        Column("show_reconciled_transactions", Boolean, nullable=False),
        Column("queue_limit_enabled", Boolean, nullable=False),
        Column("queue_limit_value", Integer, nullable=False),
        Column("queue_limit_unit", Text, nullable=False),
        Column("quick_apply_include_medium", Boolean, nullable=False),
        Column("default_category_id", Text),
        Column("updated_ts", Text, nullable=False),
    )

    try:
        old_metadata.create_all(engine)
        columns_before = {
            col["name"] for col in inspect(engine).get_columns("ynab_categorizer_config")
        }
        assert "custom_rules_json" not in columns_before

        migration = importlib.import_module(
            "migrations.versions.20260324_0007_ynab_categorizer_custom_rules"
        )

        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            operations = Operations(context)
            original_op = migration.op
            migration.op = operations
            try:
                migration.upgrade()
            finally:
                migration.op = original_op

        columns_after = {
            col["name"] for col in inspect(engine).get_columns("ynab_categorizer_config")
        }
        assert "custom_rules_json" in columns_after
    finally:
        engine.dispose()
