from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ...core import (
    Controller,
    YnabApplyEvent,
    YnabBootstrapState,
    YnabPayeeCategoryStat,
)

if TYPE_CHECKING:
    from .ynab_client import YNABClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

QUEUE_FILTER_STRICT = "strict"
QUEUE_FILTER_ALL_UNCATEGORIZED = "all_uncategorized"
QUEUE_FILTER_SKIP_TRANSFERS = "skip_transfers"

_VALID_QUEUE_MODES = {
    QUEUE_FILTER_STRICT,
    QUEUE_FILTER_ALL_UNCATEGORIZED,
    QUEUE_FILTER_SKIP_TRANSFERS,
}


class YnabCategorizerService:
    def __init__(
        self,
        ctrl: Controller,
        client: YNABClient,
        budget_id: str,
    ) -> None:
        logger.debug("YnabCategorizerService.__init__ called budget_id=%s", budget_id)
        self.ctrl = ctrl
        self.client = client
        self.budget_id = budget_id

    def is_configured(self) -> bool:
        configured = bool(self.client and self.budget_id)
        logger.debug("is_configured=%s", configured)
        return configured

    def get_config(self) -> dict[str, Any]:
        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        logger.debug("get_config queue_filter_mode=%s", cfg.queue_filter_mode)
        return {
            "queue_filter_mode": cfg.queue_filter_mode,
            "updated_ts": cfg.updated_ts,
        }

    def set_queue_filter_mode(self, queue_filter_mode: str) -> dict[str, Any]:
        logger.debug("set_queue_filter_mode called mode=%s", queue_filter_mode)
        mode = (queue_filter_mode or "").strip()
        if mode not in _VALID_QUEUE_MODES:
            raise ValueError("Invalid queue_filter_mode")
        cfg = self.ctrl.save_ynab_categorizer_config(self.budget_id, mode)
        return {
            "queue_filter_mode": cfg.queue_filter_mode,
            "updated_ts": cfg.updated_ts,
        }

    @staticmethod
    def normalize_payee(payee_name: str | None) -> str:
        if not payee_name:
            return ""
        text = payee_name.strip()
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        folded = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        folded = folded.upper()
        folded = re.sub(r"[^A-Z0-9]+", " ", folded)
        folded = re.sub(r"\s+", " ", folded).strip()
        return folded

    @staticmethod
    def confidence_label(top_count: int, total_count: int) -> tuple[float, str]:
        if total_count <= 0:
            return 0.0, "Low"
        confidence = float(top_count) / float(total_count)
        if confidence >= 0.80 and top_count >= 3:
            return confidence, "High"
        if confidence >= 0.60 and top_count >= 2:
            return confidence, "Medium"
        return confidence, "Low"

    @staticmethod
    def _is_transfer(tx: dict[str, Any]) -> bool:
        return bool(tx.get("transfer_account_id") or tx.get("transfer_transaction_id"))

    @staticmethod
    def _is_split_parent(tx: dict[str, Any]) -> bool:
        sub = tx.get("subtransactions")
        return isinstance(sub, list) and len(sub) > 0

    @staticmethod
    def _is_uncategorized(tx: dict[str, Any]) -> bool:
        return tx.get("category_id") in (None, "")

    @classmethod
    def should_include_for_queue(cls, tx: dict[str, Any], mode: str) -> bool:
        if bool(tx.get("deleted")):
            return False
        if not cls._is_uncategorized(tx):
            return False
        if mode == QUEUE_FILTER_ALL_UNCATEGORIZED:
            return True
        if mode == QUEUE_FILTER_SKIP_TRANSFERS:
            return not cls._is_transfer(tx)
        # strict default
        return (not cls._is_transfer(tx)) and (not cls._is_split_parent(tx))

    @classmethod
    def should_include_for_bootstrap(cls, tx: dict[str, Any]) -> bool:
        if bool(tx.get("deleted")):
            return False
        if cls._is_uncategorized(tx):
            return False
        if cls._is_transfer(tx):
            return False
        return not cls._is_split_parent(tx)

    @staticmethod
    def _label_sort_rank(label: str | None) -> int:
        if label == "High":
            return 0
        if label == "Medium":
            return 1
        if label == "Low":
            return 2
        return 3

    @staticmethod
    def _date_sort_value(date_str: str | None) -> int:
        if not date_str:
            return 0
        digits = re.sub(r"[^0-9]", "", date_str)
        if not digits:
            return 0
        try:
            return int(digits[:8])
        except ValueError:
            return 0

    def _categories_payload(self, groups: list[dict[str, Any]]) -> list[dict[str, str]]:
        categories: list[dict[str, str]] = []
        for group in groups:
            items = group.get("categories")
            if not isinstance(items, list):
                continue
            for cat in items:
                if not isinstance(cat, dict):
                    continue
                if cat.get("deleted"):
                    continue
                cat_id = cat.get("id")
                cat_name = cat.get("name")
                if not cat_id or not cat_name:
                    continue
                categories.append({"id": str(cat_id), "name": str(cat_name)})
        categories.sort(key=lambda c: c["name"].upper())
        return categories

    @staticmethod
    def _category_name_by_id(categories: list[dict[str, str]]) -> dict[str, str]:
        return {c["id"]: c["name"] for c in categories}

    def _build_suggestion(
        self,
        stats: list[YnabPayeeCategoryStat],
        category_name_map: dict[str, str],
    ) -> dict[str, Any] | None:
        if not stats:
            return None
        top = max(
            stats,
            key=lambda s: (
                int(s.count),
                s.last_used_at or "",
            ),
        )
        total = sum(int(s.count) for s in stats)
        confidence, label = self.confidence_label(int(top.count), int(total))
        return {
            "category_id": top.category_id,
            "category_name": category_name_map.get(top.category_id, top.category_id),
            "top_count": int(top.count),
            "total_count": int(total),
            "confidence": confidence,
            "confidence_label": label,
        }

    def get_queue(self, queue_filter_mode: str | None = None) -> dict[str, Any]:
        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        mode = (queue_filter_mode or cfg.queue_filter_mode or QUEUE_FILTER_STRICT).strip()
        if mode not in _VALID_QUEUE_MODES:
            mode = QUEUE_FILTER_STRICT

        logger.debug("get_queue called mode=%s", mode)
        transactions = self.client.get_transactions_since(None)
        category_groups = self.client.get_categories()
        categories = self._categories_payload(category_groups)
        category_name_map = self._category_name_by_id(categories)

        filtered = [tx for tx in transactions if self.should_include_for_queue(tx, mode)]
        logger.debug(
            "get_queue filtered transactions total=%s queue=%s",
            len(transactions),
            len(filtered),
        )

        payees = sorted(
            {
                self.normalize_payee(tx.get("payee_name"))
                for tx in filtered
                if self.normalize_payee(tx.get("payee_name"))
            }
        )
        stats_by_payee = self.ctrl.get_ynab_stats_for_payees(self.budget_id, payees)

        grouped: dict[str, dict[str, Any]] = {}
        for tx in filtered:
            payee_display = str(tx.get("payee_name") or "Unknown").strip() or "Unknown"
            payee_norm = self.normalize_payee(payee_display)
            if not payee_norm:
                payee_norm = "UNKNOWN"

            group = grouped.setdefault(
                payee_norm,
                {
                    "payee_normalized": payee_norm,
                    "payee_display": payee_display,
                    "transaction_ids": [],
                    "transactions": [],
                    "latest_date": None,
                },
            )

            tx_id = tx.get("id")
            tx_date = tx.get("date")
            if tx_id:
                group["transaction_ids"].append(str(tx_id))
            if isinstance(tx_date, str):
                current_latest = group["latest_date"]
                if not current_latest or tx_date > current_latest:
                    group["latest_date"] = tx_date
            group["transactions"].append(
                {
                    "id": str(tx_id),
                    "date": str(tx_date) if tx_date else "",
                    "payee_name": payee_display,
                    "memo": str(tx.get("memo") or ""),
                    "amount_milliunits": tx.get("amount"),
                }
            )

        groups: list[dict[str, Any]] = []
        for payee_norm, group in grouped.items():
            stats = stats_by_payee.get(payee_norm, [])
            suggestion = self._build_suggestion(stats, category_name_map)
            confidence_label = suggestion.get("confidence_label") if suggestion else None

            groups.append(
                {
                    "payee_normalized": payee_norm,
                    "payee_display": group["payee_display"],
                    "transaction_ids": group["transaction_ids"],
                    "transaction_count": len(group["transaction_ids"]),
                    "latest_date": group["latest_date"],
                    "transactions": group["transactions"],
                    "suggestion": suggestion,
                    "confidence_label": confidence_label,
                }
            )

        groups.sort(
            key=lambda g: (
                self._label_sort_rank(g.get("confidence_label")),
                -self._date_sort_value(g.get("latest_date")),
            )
        )

        return {
            "queue_filter_mode": mode,
            "group_count": len(groups),
            "transaction_count": len(filtered),
            "categories": categories,
            "groups": groups,
        }

    def apply_category(
        self,
        transaction_ids: list[str],
        category_id: str,
        *,
        applied_by_username: str | None,
    ) -> dict[str, Any]:
        logger.debug(
            "apply_category called transaction_count=%s category_id=%s applied_by=%s",
            len(transaction_ids),
            category_id,
            applied_by_username,
        )
        tx_ids = [str(tx_id).strip() for tx_id in transaction_ids if str(tx_id).strip()]
        tx_ids = list(dict.fromkeys(tx_ids))
        if not tx_ids:
            raise ValueError("transaction_ids must not be empty")
        if not category_id:
            raise ValueError("category_id is required")

        payload_items = [{"id": tx_id, "category_id": category_id} for tx_id in tx_ids]
        ynab_result = self.client.update_transactions_bulk(payload_items)

        all_transactions = self.client.get_transactions_since(None)
        by_id = {
            str(tx.get("id")): tx
            for tx in all_transactions
            if isinstance(tx, dict) and tx.get("id") is not None
        }

        applied_at = datetime.now(self.ctrl.finland_tz).isoformat()
        inserted_events = 0
        skipped_existing = 0

        for tx_id in tx_ids:
            tx = by_id.get(tx_id, {})
            payee_normalized = self.normalize_payee(str(tx.get("payee_name") or "")) or "UNKNOWN"
            inserted = self.ctrl.record_ynab_apply_event(
                YnabApplyEvent(
                    budget_id=self.budget_id,
                    transaction_id=tx_id,
                    payee_normalized=payee_normalized,
                    category_id=category_id,
                    applied_by_username=applied_by_username,
                    applied_at=applied_at,
                )
            )
            if inserted:
                inserted_events += 1
                self.ctrl.increment_ynab_payee_category_stat(
                    self.budget_id,
                    payee_normalized,
                    category_id,
                    last_used_at=str(tx.get("date") or applied_at),
                )
            else:
                skipped_existing += 1

        logger.debug(
            "apply_category completed tx_count=%s inserted_events=%s skipped_existing=%s",
            len(tx_ids),
            inserted_events,
            skipped_existing,
        )
        return {
            "transaction_count": len(tx_ids),
            "inserted_events": inserted_events,
            "skipped_existing": skipped_existing,
            "ynab": ynab_result,
        }

    def get_bootstrap_status(self) -> dict[str, Any]:
        state = self.ctrl.get_ynab_bootstrap_state(self.budget_id)
        logger.debug("get_bootstrap_status has_state=%s", state is not None)
        return {
            "bootstrapped": state is not None,
            "state": {
                "budget_id": state.budget_id,
                "bootstrapped_at": state.bootstrapped_at,
                "history_start_date": state.history_start_date,
                "history_end_date": state.history_end_date,
            }
            if state is not None
            else None,
        }

    def bootstrap(self, *, force: bool = False) -> dict[str, Any]:
        logger.debug("bootstrap called force=%s", force)
        existing = self.ctrl.get_ynab_bootstrap_state(self.budget_id)
        if existing is not None and not force:
            logger.debug("bootstrap skipped existing state and force=False")
            return {
                "skipped": True,
                "reason": "already_bootstrapped",
                "state": {
                    "budget_id": existing.budget_id,
                    "bootstrapped_at": existing.bootstrapped_at,
                    "history_start_date": existing.history_start_date,
                    "history_end_date": existing.history_end_date,
                },
            }

        today = date.today()
        start_date = today - timedelta(days=730)
        start_date_iso = start_date.isoformat()
        end_date_iso = today.isoformat()
        now_iso = datetime.now(self.ctrl.finland_tz).isoformat()

        transactions = self.client.get_transactions_since(start_date_iso)
        aggregate: dict[tuple[str, str], dict[str, Any]] = {}

        for tx in transactions:
            if not self.should_include_for_bootstrap(tx):
                continue
            payee_norm = self.normalize_payee(str(tx.get("payee_name") or "")) or "UNKNOWN"
            category_id = str(tx.get("category_id") or "").strip()
            if not category_id:
                continue
            key = (payee_norm, category_id)
            bucket = aggregate.setdefault(
                key,
                {
                    "count": 0,
                    "last_used_at": None,
                },
            )
            bucket["count"] += 1
            tx_date = str(tx.get("date") or "")
            if tx_date:
                current_last = bucket["last_used_at"]
                if not current_last or tx_date > current_last:
                    bucket["last_used_at"] = tx_date

        stats_rows: list[YnabPayeeCategoryStat] = []
        for (payee_norm, category_id), bucket in aggregate.items():
            stats_rows.append(
                YnabPayeeCategoryStat(
                    budget_id=self.budget_id,
                    payee_normalized=payee_norm,
                    category_id=category_id,
                    count=int(bucket["count"]),
                    last_used_at=bucket["last_used_at"],
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )

        self.ctrl.replace_ynab_payee_category_stats(self.budget_id, stats_rows)
        state = YnabBootstrapState(
            budget_id=self.budget_id,
            bootstrapped_at=now_iso,
            history_start_date=start_date_iso,
            history_end_date=end_date_iso,
        )
        self.ctrl.save_ynab_bootstrap_state(state)

        logger.debug(
            "bootstrap completed stats_rows=%s start=%s end=%s",
            len(stats_rows),
            start_date_iso,
            end_date_iso,
        )
        return {
            "skipped": False,
            "seeded_rows": len(stats_rows),
            "history_start_date": start_date_iso,
            "history_end_date": end_date_iso,
            "bootstrapped_at": now_iso,
        }
