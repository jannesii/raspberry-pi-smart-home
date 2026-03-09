from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.models import YnabCategorizerConfig, YnabPayeeCategoryStat
from app.services.ynab.ynab_categorizer_service import YnabCategorizerService


@dataclass
class _CtrlStub:
    mode: str = "strict"
    default_category_id: str | None = None

    def __post_init__(self):
        self.finland_tz = ZoneInfo("Europe/Helsinki")

    def get_ynab_categorizer_config(self, budget_id: str):
        return YnabCategorizerConfig(
            id=1,
            budget_id=budget_id,
            queue_filter_mode=self.mode,
            default_category_id=self.default_category_id,
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


class _ClientStub:
    def get_transactions_since(self, since_date):
        return [
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
            },
        ]

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

    assert payload["transaction_count"] == 3
    assert payload["quick_apply_include_medium"] is False
    ids = {tx["id"] for g in payload["groups"] for tx in g["transactions"]}
    assert ids == {"tx1", "tx2", "tx6"}
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
    assert payload["groups"][1]["payee_normalized"] == "TAXI HELSINKI"
    assert payload["groups"][1]["confidence_label"] == "Low"


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
