from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.models import YnabCategorizerConfig, YnabPayeeCategoryStat
from app.services.ynab.ynab_categorizer_service import YnabCategorizerService


@dataclass
class _CtrlStub:
    mode: str = "strict"
    test_mode_enabled: bool = False
    default_category_id: str | None = None
    custom_rules: list[dict] | None = None
    recorded_events: list[tuple[str, str, str]] | None = None
    incremented_stats: list[tuple[str, str, str, str]] | None = None

    def __post_init__(self):
        self.finland_tz = ZoneInfo("Europe/Helsinki")
        self.recorded_events = []
        self.incremented_stats = []

    def get_ynab_categorizer_config(self, budget_id: str):
        return YnabCategorizerConfig(
            id=1,
            budget_id=budget_id,
            queue_filter_mode=self.mode,
            test_mode_enabled=self.test_mode_enabled,
            default_category_id=self.default_category_id,
            custom_rules_json=json.dumps(self.custom_rules)
            if self.custom_rules is not None
            else None,
            updated_ts=datetime.now(self.finland_tz).isoformat(),
        )

    def get_ynab_stats_for_payees(self, budget_id: str, payees: list[str]):
        return {
            "K MARKET": [
                YnabPayeeCategoryStat(
                    id=1,
                    budget_id=budget_id,
                    payee_normalized="K MARKET",
                    category_id="cat_groceries",
                    count=8,
                    last_used_at="2026-03-01",
                    created_at="2026-03-01T00:00:00+02:00",
                    updated_at="2026-03-01T00:00:00+02:00",
                ),
                YnabPayeeCategoryStat(
                    id=2,
                    budget_id=budget_id,
                    payee_normalized="K MARKET",
                    category_id="cat_misc",
                    count=2,
                    last_used_at="2026-02-01",
                    created_at="2026-02-01T00:00:00+02:00",
                    updated_at="2026-02-01T00:00:00+02:00",
                ),
            ],
            "TAXI HELSINKI": [
                YnabPayeeCategoryStat(
                    id=3,
                    budget_id=budget_id,
                    payee_normalized="TAXI HELSINKI",
                    category_id="cat_transport",
                    count=2,
                    last_used_at="2026-03-02",
                    created_at="2026-03-02T00:00:00+02:00",
                    updated_at="2026-03-02T00:00:00+02:00",
                ),
                YnabPayeeCategoryStat(
                    id=4,
                    budget_id=budget_id,
                    payee_normalized="TAXI HELSINKI",
                    category_id="cat_misc",
                    count=2,
                    last_used_at="2026-01-01",
                    created_at="2026-01-01T00:00:00+02:00",
                    updated_at="2026-01-01T00:00:00+02:00",
                ),
            ],
        }

    def get_ynab_category_usage_counts(self, budget_id: str):
        return {
            "cat_transport": 10,
            "cat_groceries": 8,
            "cat_misc": 2,
        }

    def record_ynab_apply_event(self, event):
        self.recorded_events.append(
            (event.transaction_id, event.category_id, event.payee_normalized)
        )
        return True

    def increment_ynab_payee_category_stat(
        self,
        budget_id: str,
        payee_normalized: str,
        category_id: str,
        *,
        last_used_at: str,
    ):
        self.incremented_stats.append((budget_id, payee_normalized, category_id, last_used_at))


class _ClientStub:
    def __init__(self):
        self.bulk_updates = []

    def get_transactions_since(self, since_date, *, transaction_type=None):
        transactions = [
            {
                "id": "tx1",
                "date": "2026-03-05",
                "payee_name": "K-Märket",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [],
                "memo": "Groceries",
                "amount": -15990,
                "approved": False,
            },
            {
                "id": "tx2",
                "date": "2026-03-07",
                "payee_name": "Taxi Helsinki",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [],
                "memo": "Airport",
                "amount": -25990,
                "approved": True,
            },
            {
                "id": "tx3",
                "date": "2026-03-08",
                "payee_name": "Transfer test",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": "account_x",
                "subtransactions": [],
                "memo": "Transfer",
                "amount": -1000,
                "approved": False,
            },
            {
                "id": "tx4",
                "date": "2026-03-09",
                "payee_name": "Split parent",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [{"id": "sub1"}],
                "memo": "Split",
                "amount": -2000,
                "approved": False,
            },
            {
                "id": "tx5",
                "date": "2026-03-06",
                "payee_name": "Reconciled store",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [],
                "memo": "Reconciled",
                "amount": -3990,
                "cleared": "reconciled",
                "approved": False,
            },
            {
                "id": "tx6",
                "date": "2025-01-01",
                "payee_name": "Old tx",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [],
                "memo": "Old uncategorized",
                "amount": -2990,
                "approved": False,
            },
            {
                "id": "tx7",
                "date": "2026-02-26",
                "payee_name": "Starting Balance",
                "payee_id": "starting_balance",
                "account_name": "Nordea Everyday",
                "category_id": None,
                "deleted": False,
                "transfer_account_id": None,
                "subtransactions": [],
                "memo": "",
                "amount": 610000,
                "approved": True,
            },
        ]
        if transaction_type == "unapproved":
            return [tx for tx in transactions if not bool(tx.get("approved"))]
        return transactions

    def update_transactions_bulk(self, items):
        self.bulk_updates.append(items)
        return {"transaction_ids": [item.get("id") for item in items]}

    def get_categories(self):
        return [
            {
                "id": "group_1",
                "name": "Group",
                "categories": [
                    {"id": "cat_groceries", "name": "Groceries", "deleted": False},
                    {"id": "cat_transport", "name": "Transport", "deleted": False},
                    {"id": "cat_misc", "name": "Misc", "deleted": False},
                    {"id": "cat_hidden", "name": "Hidden", "deleted": False, "hidden": True},
                ],
            }
        ]


def test_normalize_payee_diacritics_and_symbols():
    normalized = YnabCategorizerService.normalize_payee("  K-Märket!!  ")
    assert normalized == "K MARKET"


def test_confidence_label_thresholds():
    confidence, label = YnabCategorizerService.confidence_label(8, 10)
    assert confidence == 0.8
    assert label == "High"

    confidence, label = YnabCategorizerService.confidence_label(2, 3)
    assert confidence > 0.6
    assert label == "Medium"

    confidence, label = YnabCategorizerService.confidence_label(1, 10)
    assert label == "Low"


def test_queue_filtering_strict_skips_transfer_and_split_parent():
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()

    assert payload["transaction_count"] == 5
    assert payload["test_mode_enabled"] is False
    assert payload["quick_apply_include_medium"] is False
    assert payload["needs_category_count"] == 5
    assert payload["needs_approval_count"] == 4
    ids = {tx["id"] for g in payload["groups"] for tx in g["transactions"]}
    assert ids == {"tx1", "tx2", "tx3", "tx4", "tx6"}
    assert all(
        tx["account_name"] == "Nordea Everyday"
        for g in payload["groups"]
        for tx in g["transactions"]
    )
    assert [cat["id"] for cat in payload["categories"][:3]] == [
        "cat_transport",
        "cat_groceries",
        "cat_misc",
    ]
    assert all(cat["id"] != "cat_hidden" for cat in payload["categories"])


def test_group_sort_by_confidence_then_latest_date_desc():
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()

    assert payload["groups"][0]["payee_normalized"] == "K MARKET"
    assert payload["groups"][0]["confidence_label"] == "High"
    assert any(group["payee_normalized"] == "TAXI HELSINKI" for group in payload["groups"])


def test_get_config_includes_test_mode_flag():
    ctrl = _CtrlStub(mode="strict", test_mode_enabled=True)
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_config()

    assert payload["test_mode_enabled"] is True


def test_apply_category_in_test_mode_skips_remote_and_local_writes():
    ctrl = _CtrlStub(test_mode_enabled=True)
    client = _ClientStub()
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.apply_category(
        transaction_ids=["tx1", "tx2"],
        category_id="cat_groceries",
        applied_by_username="root",
    )

    assert result["simulated"] is True
    assert result["test_mode_enabled"] is True
    assert result["transaction_count"] == 2
    assert client.bulk_updates == []
    assert ctrl.recorded_events == []
    assert ctrl.incremented_stats == []


def test_approve_transactions_in_test_mode_skips_remote_write():
    ctrl = _CtrlStub(test_mode_enabled=True)
    client = _ClientStub()
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.approve_transactions(
        transaction_ids=["tx1", "tx2"],
        approved_by_username="root",
    )

    assert result["simulated"] is True
    assert result["test_mode_enabled"] is True
    assert result["approved_count"] == 2
    assert client.bulk_updates == []


def test_reconciled_transactions_hidden_by_default():
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()
    ids = {tx["id"] for g in payload["groups"] for tx in g["transactions"]}

    assert "tx5" not in ids


def test_starting_balance_hidden_from_queue():
    ctrl = _CtrlStub(mode="all_uncategorized")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()
    ids = {tx["id"] for g in payload["groups"] for tx in g["transactions"]}

    assert "tx7" not in ids


def test_default_category_used_when_no_payee_suggestion():
    ctrl = _CtrlStub(mode="strict", default_category_id="cat_misc")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()
    by_payee = {group["payee_normalized"]: group for group in payload["groups"]}
    old_tx_group = by_payee["OLD TX"]

    assert old_tx_group["suggestion"] is not None
    assert old_tx_group["suggestion"]["category_id"] == "cat_misc"
    assert old_tx_group["suggestion"]["source"] == "default"


def test_custom_rule_overrides_learned_suggestion():
    ctrl = _CtrlStub(
        mode="strict",
        custom_rules=[
            {
                "id": "rule-fuel",
                "enabled": True,
                "payee_match_type": "contains",
                "payee_value": "k märket",
                "amount_operator": "any",
                "amount_value_eur": None,
                "category_id": "cat_misc",
            }
        ],
    )
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_queue()
    by_payee = {group["payee_normalized"]: group for group in payload["groups"]}
    group = by_payee["K MARKET"]

    assert group["suggestion"] is not None
    assert group["suggestion"]["source"] == "rule"
    assert group["suggestion"]["matched_rule_id"] == "rule-fuel"
    assert group["suggestion"]["category_id"] == "cat_misc"


def test_already_categorized_unapproved_uses_current_category():
    class _ClientWithCurrentCategory(_ClientStub):
        def get_transactions_since(self, since_date, *, transaction_type=None):
            txs = super().get_transactions_since(since_date, transaction_type=transaction_type)
            if transaction_type == "unapproved":
                return txs
            copied = [dict(tx) for tx in txs]
            copied[0]["category_id"] = "cat_transport"
            copied[0]["category_name"] = "Transport"
            return copied

    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(
        ctrl=ctrl, client=_ClientWithCurrentCategory(), budget_id="budget1"
    )

    payload = svc.get_queue()
    k_market_group = next(
        group for group in payload["groups"] if group["payee_normalized"] == "K MARKET"
    )

    assert k_market_group["suggestion"] is not None
    assert k_market_group["suggestion"]["source"] == "current_category"
    assert k_market_group["suggestion"]["category_id"] == "cat_transport"


def test_queue_limit_filters_out_old_transactions():
    tx = {"id": "old", "date": "2025-01-01", "deleted": False}
    included = YnabCategorizerService.should_include_by_limit(
        tx,
        queue_limit_enabled=True,
        queue_limit_value=30,
        queue_limit_unit="days",
        now=date(2026, 3, 9),
    )
    assert included is False


def test_approval_queue_returns_only_unapproved_transactions():
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    payload = svc.get_approval_queue()
    ids = [tx["id"] for tx in payload["transactions"]]

    assert payload["transaction_count"] == len(ids)
    assert "tx2" not in ids
    assert "tx7" not in ids
    assert "tx3" in ids


def test_approve_transactions_updates_bulk():
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=_ClientStub(), budget_id="budget1")

    result = svc.approve_transactions(["tx1", "tx3"], approved_by_username="root")
    assert result["approved_count"] == 2


def test_apply_category_marks_transactions_approved_in_bulk_payload():
    class _ClientCapture(_ClientStub):
        def __init__(self):
            super().__init__()
            self.payloads = []

        def update_transactions_bulk(self, items):
            self.payloads.append(items)
            return super().update_transactions_bulk(items)

    class _CtrlApplyStub(_CtrlStub):
        def __post_init__(self):
            super().__post_init__()
            self.recorded = []
            self.incremented = []

        def record_ynab_apply_event(self, event):
            self.recorded.append(event)
            return True

        def increment_ynab_payee_category_stat(
            self, budget_id, payee_normalized, category_id, last_used_at=None
        ):
            self.incremented.append((budget_id, payee_normalized, category_id, last_used_at))

    client = _ClientCapture()
    ctrl = _CtrlApplyStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.apply_category(["tx1", "tx3"], "cat_transport", applied_by_username="root")

    assert result["approved_count"] == 2
    assert client.payloads[0] == [
        {"id": "tx1", "category_id": "cat_transport", "approved": True},
        {"id": "tx3", "category_id": "cat_transport", "approved": True},
    ]


def test_review_commit_splits_changed_and_approval_only_rows():
    class _ClientWithCurrentCategory(_ClientStub):
        def get_transactions_since(self, since_date, *, transaction_type=None):
            txs = super().get_transactions_since(since_date, transaction_type=transaction_type)
            copied = [dict(tx) for tx in txs]
            copied[0]["category_id"] = "cat_groceries"
            copied[0]["category_name"] = "Groceries"
            copied[0]["approved"] = False
            copied[1]["category_id"] = "cat_transport"
            copied[1]["category_name"] = "Transport"
            copied[1]["approved"] = False
            return copied

    client = _ClientWithCurrentCategory()
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.review_commit(
        transactions=[
            {"id": "tx1", "category_id": "cat_misc"},
            {"id": "tx2", "category_id": "cat_transport"},
        ],
        applied_by_username="root",
    )

    assert result["transaction_count"] == 2
    assert result["approved_count"] == 2
    assert result["categorized_count"] == 1
    assert result["approval_only_count"] == 1
    assert client.bulk_updates[0] == [
        {"id": "tx1", "category_id": "cat_misc", "approved": True},
        {"id": "tx2", "approved": True},
    ]
    assert ctrl.recorded_events == [("tx1", "cat_misc", "K MARKET")]
    assert ctrl.incremented_stats == [("budget1", "K MARKET", "cat_misc", "2026-03-05")]


def test_review_commit_in_test_mode_skips_remote_and_local_writes():
    ctrl = _CtrlStub(test_mode_enabled=True)
    client = _ClientStub()
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.review_commit(
        transactions=[{"id": "tx1", "category_id": "cat_groceries"}],
        applied_by_username="root",
    )

    assert result["simulated"] is True
    assert result["categorized_count"] == 1
    assert result["approval_only_count"] == 0
    assert client.bulk_updates == []
    assert ctrl.recorded_events == []
    assert ctrl.incremented_stats == []


def test_review_commit_chunks_bulk_updates_over_200_items():
    class _ClientManyTransactions(_ClientStub):
        def get_transactions_since(self, since_date, *, transaction_type=None):
            transactions = []
            for index in range(205):
                transactions.append(
                    {
                        "id": f"tx{index}",
                        "date": "2026-03-09",
                        "payee_name": f"Store {index}",
                        "account_name": "Nordea Everyday",
                        "category_id": None,
                        "deleted": False,
                        "transfer_account_id": None,
                        "subtransactions": [],
                        "memo": "",
                        "amount": -1000,
                        "approved": False,
                    }
                )
            if transaction_type == "unapproved":
                return transactions
            return transactions

    client = _ClientManyTransactions()
    ctrl = _CtrlStub(mode="strict")
    svc = YnabCategorizerService(ctrl=ctrl, client=client, budget_id="budget1")

    result = svc.review_commit(
        transactions=[{"id": f"tx{index}", "category_id": "cat_misc"} for index in range(205)],
        applied_by_username="root",
    )

    assert result["transaction_count"] == 205
    assert result["categorized_count"] == 205
    assert len(client.bulk_updates) == 2
    assert len(client.bulk_updates[0]) == 200
    assert len(client.bulk_updates[1]) == 5
