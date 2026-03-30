from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

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
_VALID_QUEUE_LIMIT_UNITS = {"days", "months", "years"}
_VALID_RULE_PAYEE_MATCH_TYPES = {"contains", "equals"}
_VALID_RULE_AMOUNT_OPERATORS = {"any", "over", "under"}


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
        custom_rules = self._parse_custom_rules_json(cfg.custom_rules_json)
        logger.debug(
            (
                "get_config queue_filter_mode=%s test_mode_enabled=%s show_reconciled_transactions=%s "
                "queue_limit_enabled=%s queue_limit_value=%s queue_limit_unit=%s "
                "quick_apply_include_medium=%s default_category_id=%s custom_rules=%s"
            ),
            cfg.queue_filter_mode,
            cfg.test_mode_enabled,
            cfg.show_reconciled_transactions,
            cfg.queue_limit_enabled,
            cfg.queue_limit_value,
            cfg.queue_limit_unit,
            cfg.quick_apply_include_medium,
            cfg.default_category_id,
            len(custom_rules),
        )
        return {
            "queue_filter_mode": cfg.queue_filter_mode,
            "test_mode_enabled": bool(cfg.test_mode_enabled),
            "show_reconciled_transactions": bool(cfg.show_reconciled_transactions),
            "queue_limit_enabled": bool(cfg.queue_limit_enabled),
            "queue_limit_value": int(cfg.queue_limit_value),
            "queue_limit_unit": str(cfg.queue_limit_unit),
            "quick_apply_include_medium": bool(cfg.quick_apply_include_medium),
            "default_category_id": cfg.default_category_id,
            "custom_rules": custom_rules,
            "updated_ts": cfg.updated_ts,
        }

    def set_queue_filter_mode(self, queue_filter_mode: str) -> dict[str, Any]:
        logger.debug("set_queue_filter_mode called mode=%s", queue_filter_mode)
        current = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        return self.set_config(
            queue_filter_mode=queue_filter_mode,
            test_mode_enabled=bool(current.test_mode_enabled),
            show_reconciled_transactions=bool(current.show_reconciled_transactions),
            queue_limit_enabled=bool(current.queue_limit_enabled),
            queue_limit_value=int(current.queue_limit_value),
            queue_limit_unit=str(current.queue_limit_unit),
            quick_apply_include_medium=bool(current.quick_apply_include_medium),
            default_category_id=current.default_category_id,
            custom_rules=self._parse_custom_rules_json(current.custom_rules_json),
        )

    def set_config(
        self,
        *,
        queue_filter_mode: str,
        test_mode_enabled: bool,
        show_reconciled_transactions: bool,
        queue_limit_enabled: bool,
        queue_limit_value: int,
        queue_limit_unit: str,
        quick_apply_include_medium: bool,
        default_category_id: str | None,
        custom_rules: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        logger.debug(
            (
                "set_config called mode=%s test_mode_enabled=%s show_reconciled=%s limit_enabled=%s "
                "limit_value=%s limit_unit=%s quick_apply_include_medium=%s "
                "default_category_id=%s custom_rules_supplied=%s"
            ),
            queue_filter_mode,
            test_mode_enabled,
            show_reconciled_transactions,
            queue_limit_enabled,
            queue_limit_value,
            queue_limit_unit,
            quick_apply_include_medium,
            default_category_id,
            custom_rules is not None,
        )
        mode = (queue_filter_mode or "").strip()
        if mode not in _VALID_QUEUE_MODES:
            raise ValueError("Invalid queue_filter_mode")
        limit_unit_value = str(queue_limit_unit or "").strip().lower()
        if limit_unit_value not in _VALID_QUEUE_LIMIT_UNITS:
            raise ValueError("Invalid queue_limit_unit")
        if int(queue_limit_value) < 1:
            raise ValueError("Invalid queue_limit_value")

        current = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        validated_custom_rules = (
            self._parse_custom_rules_json(current.custom_rules_json)
            if custom_rules is None
            else self._validate_custom_rules(custom_rules)
        )
        custom_rules_json = self._serialize_custom_rules(validated_custom_rules)

        cfg = self.ctrl.save_ynab_categorizer_config(
            self.budget_id,
            mode,
            test_mode_enabled=bool(test_mode_enabled),
            show_reconciled_transactions=bool(show_reconciled_transactions),
            queue_limit_enabled=bool(queue_limit_enabled),
            queue_limit_value=int(queue_limit_value),
            queue_limit_unit=limit_unit_value,
            quick_apply_include_medium=bool(quick_apply_include_medium),
            default_category_id=str(default_category_id or "").strip() or None,
            custom_rules_json=custom_rules_json,
        )
        return {
            "queue_filter_mode": cfg.queue_filter_mode,
            "test_mode_enabled": bool(cfg.test_mode_enabled),
            "show_reconciled_transactions": bool(cfg.show_reconciled_transactions),
            "queue_limit_enabled": bool(cfg.queue_limit_enabled),
            "queue_limit_value": int(cfg.queue_limit_value),
            "queue_limit_unit": str(cfg.queue_limit_unit),
            "quick_apply_include_medium": bool(cfg.quick_apply_include_medium),
            "default_category_id": cfg.default_category_id,
            "custom_rules": validated_custom_rules,
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
    def _amount_eur_from_tx(tx: dict[str, Any]) -> float | None:
        amount_raw = tx.get("amount")
        try:
            return abs(float(amount_raw)) / 1000.0
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_rule_bool(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off", ""}:
                return False
        raise ValueError("Invalid custom rule boolean value")

    @classmethod
    def _normalize_custom_rule(cls, rule: dict[str, Any], index: int) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise ValueError(f"custom_rules[{index}] must be an object")

        rule_id = str(rule.get("id") or "").strip() or f"rule-{uuid4().hex[:12]}"
        enabled = cls._coerce_rule_bool(rule.get("enabled"), default=True)
        payee_match_type = str(rule.get("payee_match_type") or "").strip().lower()
        if payee_match_type not in _VALID_RULE_PAYEE_MATCH_TYPES:
            raise ValueError(f"custom_rules[{index}].payee_match_type is invalid")

        payee_value = str(rule.get("payee_value") or "").strip()
        if not payee_value:
            raise ValueError(f"custom_rules[{index}].payee_value is required")

        amount_operator = str(rule.get("amount_operator") or "any").strip().lower()
        if amount_operator not in _VALID_RULE_AMOUNT_OPERATORS:
            raise ValueError(f"custom_rules[{index}].amount_operator is invalid")

        amount_value_eur: float | None = None
        if amount_operator != "any":
            try:
                amount_value_eur = float(rule.get("amount_value_eur"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"custom_rules[{index}].amount_value_eur is invalid") from exc
            if amount_value_eur <= 0:
                raise ValueError(f"custom_rules[{index}].amount_value_eur must be positive")

        category_id = str(rule.get("category_id") or "").strip()
        if not category_id:
            raise ValueError(f"custom_rules[{index}].category_id is required")

        return {
            "id": rule_id,
            "enabled": enabled,
            "payee_match_type": payee_match_type,
            "payee_value": payee_value,
            "amount_operator": amount_operator,
            "amount_value_eur": amount_value_eur,
            "category_id": category_id,
        }

    @classmethod
    def _validate_custom_rules(cls, custom_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        logger.debug("validate_custom_rules called count=%s", len(custom_rules))
        if not isinstance(custom_rules, list):
            raise ValueError("custom_rules must be a list")
        normalized = [
            cls._normalize_custom_rule(rule, index) for index, rule in enumerate(custom_rules)
        ]
        logger.debug("validate_custom_rules completed count=%s", len(normalized))
        return normalized

    @classmethod
    def _parse_custom_rules_json(cls, custom_rules_json: str | None) -> list[dict[str, Any]]:
        if not custom_rules_json:
            return []
        try:
            parsed = json.loads(custom_rules_json)
        except (TypeError, ValueError):
            logger.warning("Failed to parse custom_rules_json")
            return []
        if not isinstance(parsed, list):
            logger.warning("custom_rules_json did not decode to a list")
            return []

        custom_rules: list[dict[str, Any]] = []
        for index, raw_rule in enumerate(parsed):
            try:
                custom_rules.append(cls._normalize_custom_rule(raw_rule, index))
            except ValueError as exc:
                logger.warning(
                    "Ignoring invalid persisted custom rule index=%s error=%s", index, exc
                )
        return custom_rules

    @staticmethod
    def _serialize_custom_rules(custom_rules: list[dict[str, Any]]) -> str | None:
        if not custom_rules:
            return None
        return json.dumps(custom_rules, ensure_ascii=True, separators=(",", ":"))

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
    def _is_starting_balance(cls, tx: dict[str, Any]) -> bool:
        payee_name = str(tx.get("payee_name") or "").strip()
        if cls.normalize_payee(payee_name) == "STARTING BALANCE":
            return True
        payee_id = str(tx.get("payee_id") or "").strip().lower()
        return payee_id in {"starting_balance", "starting-balance"}

    @staticmethod
    def _is_reconciled(tx: dict[str, Any]) -> bool:
        return str(tx.get("cleared") or "").strip().lower() == "reconciled"

    @staticmethod
    def _is_unapproved(tx: dict[str, Any]) -> bool:
        return not bool(tx.get("approved"))

    @staticmethod
    def _parse_tx_date(tx: dict[str, Any]) -> date | None:
        tx_date = tx.get("date")
        if not tx_date:
            return None
        try:
            return date.fromisoformat(str(tx_date))
        except ValueError:
            return None

    @staticmethod
    def _subtract_months(value: date, months: int) -> date:
        year = value.year
        month = value.month - months
        while month <= 0:
            month += 12
            year -= 1
        month_days = [
            31,
            29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ]
        day = min(value.day, month_days[month - 1])
        return date(year, month, day)

    @staticmethod
    def _subtract_years(value: date, years: int) -> date:
        year = value.year - years
        month = value.month
        day = value.day
        if month == 2 and day == 29:
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            if not leap:
                day = 28
        return date(year, month, day)

    @classmethod
    def _limit_cutoff_date(cls, *, now: date, limit_value: int, limit_unit: str) -> date:
        if limit_unit == "days":
            return now - timedelta(days=limit_value)
        if limit_unit == "months":
            return cls._subtract_months(now, limit_value)
        return cls._subtract_years(now, limit_value)

    @classmethod
    def should_include_for_queue(cls, tx: dict[str, Any], mode: str) -> bool:
        if bool(tx.get("deleted")):
            return False
        if cls._is_starting_balance(tx):
            return False
        if not cls._is_uncategorized(tx):
            return False
        if mode == QUEUE_FILTER_ALL_UNCATEGORIZED:
            return True
        if mode == QUEUE_FILTER_SKIP_TRANSFERS:
            return not cls._is_transfer(tx)
        return (not cls._is_transfer(tx)) and (not cls._is_split_parent(tx))

    @classmethod
    def should_include_for_review(cls, tx: dict[str, Any], mode: str) -> bool:
        if bool(tx.get("deleted")):
            return False
        if cls._is_starting_balance(tx):
            return False
        if cls._is_unapproved(tx):
            return True
        return cls.should_include_for_queue(tx, mode)

    @classmethod
    def should_include_reconciled(cls, tx: dict[str, Any], show_reconciled: bool) -> bool:
        if show_reconciled:
            return True
        return not cls._is_reconciled(tx)

    @classmethod
    def should_include_by_limit(
        cls,
        tx: dict[str, Any],
        *,
        queue_limit_enabled: bool,
        queue_limit_value: int,
        queue_limit_unit: str,
        now: date | None = None,
    ) -> bool:
        if not queue_limit_enabled:
            return True
        tx_date = cls._parse_tx_date(tx)
        if tx_date is None:
            return False
        if queue_limit_value < 1 or queue_limit_unit not in _VALID_QUEUE_LIMIT_UNITS:
            return True
        today = now or date.today()
        cutoff = cls._limit_cutoff_date(
            now=today,
            limit_value=queue_limit_value,
            limit_unit=queue_limit_unit,
        )
        return tx_date >= cutoff

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
        ranking = {
            "Rule": 0,
            "High": 1,
            "Current": 2,
            "Medium": 3,
            "Default": 4,
            "Low": 5,
        }
        return ranking.get(str(label), 6)

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

    @staticmethod
    def _queue_transaction_payload(
        tx: dict[str, Any],
        payee_display: str,
        *,
        needs_category: bool,
        needs_approval: bool,
        resolved: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tx_id = tx.get("id")
        tx_date = tx.get("date")
        current_category_id = str(tx.get("category_id") or "") or None
        current_category_name = str(tx.get("category_name") or "") or None
        return {
            "id": str(tx_id),
            "date": str(tx_date) if tx_date else "",
            "payee_name": payee_display,
            "account_name": str(tx.get("account_name") or ""),
            "memo": str(tx.get("memo") or ""),
            "amount_milliunits": tx.get("amount"),
            "category_id": current_category_id,
            "category_name": current_category_name,
            "current_category_id": current_category_id,
            "current_category_name": current_category_name,
            "approved": bool(tx.get("approved")),
            "cleared": str(tx.get("cleared") or ""),
            "needs_category": bool(needs_category),
            "needs_approval": bool(needs_approval),
            "resolved_category_id": (
                str(resolved.get("category_id") or "") or None if resolved else None
            ),
            "resolved_category_name": (
                str(resolved.get("category_name") or "") or None if resolved else None
            ),
            "resolved_source": (str(resolved.get("source") or "none") if resolved else "none"),
            "matched_rule_id": (
                str(resolved.get("matched_rule_id") or "") or None if resolved else None
            ),
        }

    def _categories_payload(
        self,
        groups: list[dict[str, Any]],
        *,
        usage_counts: dict[str, int],
    ) -> list[dict[str, str]]:
        by_id: dict[str, dict[str, str]] = {}
        for group in groups:
            items = group.get("categories")
            if not isinstance(items, list):
                continue
            for cat in items:
                if not isinstance(cat, dict):
                    continue
                if cat.get("deleted") or cat.get("hidden"):
                    continue
                cat_id = cat.get("id")
                cat_name = cat.get("name")
                if not cat_id or not cat_name:
                    continue
                by_id[str(cat_id)] = {"id": str(cat_id), "name": str(cat_name)}

        categories = list(by_id.values())
        categories_by_id = {item["id"]: item for item in categories}
        ranked_ids = sorted(
            categories_by_id.keys(),
            key=lambda cat_id: (
                -int(usage_counts.get(cat_id, 0)),
                categories_by_id[cat_id]["name"].upper(),
            ),
        )
        top_ids = [cat_id for cat_id in ranked_ids if int(usage_counts.get(cat_id, 0)) > 0][:10]
        top_set = set(top_ids)
        top_categories = [categories_by_id[cat_id] for cat_id in top_ids]
        remaining_categories = [item for item in categories if item["id"] not in top_set]
        remaining_categories.sort(key=lambda c: c["name"].upper())
        ordered = top_categories + remaining_categories
        logger.debug(
            "categories payload built total=%s top_used=%s",
            len(ordered),
            len(top_categories),
        )
        return ordered

    @staticmethod
    def _category_name_by_id(categories: list[dict[str, str]]) -> dict[str, str]:
        return {c["id"]: c["name"] for c in categories}

    def _build_suggestion(
        self,
        stats: list[YnabPayeeCategoryStat],
        category_name_map: dict[str, str],
    ) -> dict[str, Any] | None:
        valid_stats = [stat for stat in stats if stat.category_id in category_name_map]
        if not valid_stats:
            return None
        top = max(
            valid_stats,
            key=lambda s: (
                int(s.count),
                s.last_used_at or "",
            ),
        )
        total = sum(int(s.count) for s in valid_stats)
        confidence, label = self.confidence_label(int(top.count), int(total))
        return {
            "category_id": top.category_id,
            "category_name": category_name_map.get(top.category_id, top.category_id),
            "top_count": int(top.count),
            "total_count": int(total),
            "confidence": confidence,
            "confidence_label": label,
            "source": "learned",
            "matched_rule_id": None,
        }

    @classmethod
    def _rule_matches_transaction(cls, tx: dict[str, Any], rule: dict[str, Any]) -> bool:
        if not bool(rule.get("enabled")):
            return False

        payee_name = cls.normalize_payee(str(tx.get("payee_name") or ""))
        rule_payee = cls.normalize_payee(str(rule.get("payee_value") or ""))
        if not payee_name or not rule_payee:
            return False

        match_type = str(rule.get("payee_match_type") or "contains")
        if match_type == "equals":
            if payee_name != rule_payee:
                return False
        elif rule_payee not in payee_name:
            return False

        amount_operator = str(rule.get("amount_operator") or "any")
        if amount_operator == "any":
            return True

        amount_eur = cls._amount_eur_from_tx(tx)
        if amount_eur is None:
            return False
        amount_threshold = rule.get("amount_value_eur")
        if amount_threshold is None:
            return False
        threshold_value = float(amount_threshold)
        if amount_operator == "over":
            return amount_eur > threshold_value
        return amount_eur < threshold_value

    def _build_rule_suggestion(
        self,
        tx: dict[str, Any],
        *,
        custom_rules: list[dict[str, Any]],
        category_name_map: dict[str, str],
    ) -> dict[str, Any] | None:
        for rule in custom_rules:
            category_id = str(rule.get("category_id") or "").strip()
            if not category_id or category_id not in category_name_map:
                continue
            if not self._rule_matches_transaction(tx, rule):
                continue
            return {
                "category_id": category_id,
                "category_name": category_name_map[category_id],
                "confidence": 1.0,
                "confidence_label": "Rule",
                "source": "rule",
                "matched_rule_id": str(rule.get("id") or "").strip() or None,
            }
        return None

    @staticmethod
    def _current_category_suggestion(tx: dict[str, Any]) -> dict[str, Any] | None:
        category_id = str(tx.get("category_id") or "").strip()
        category_name = str(tx.get("category_name") or "").strip()
        if not category_id:
            return None
        return {
            "category_id": category_id,
            "category_name": category_name or category_id,
            "confidence": 1.0,
            "confidence_label": "Current",
            "source": "current_category",
            "matched_rule_id": None,
        }

    def _resolve_transaction_suggestion(
        self,
        tx: dict[str, Any],
        *,
        custom_rules: list[dict[str, Any]],
        payee_norm: str,
        stats_by_payee: dict[str, list[YnabPayeeCategoryStat]],
        category_name_map: dict[str, str],
        default_category_id: str | None,
    ) -> dict[str, Any] | None:
        rule_suggestion = self._build_rule_suggestion(
            tx,
            custom_rules=custom_rules,
            category_name_map=category_name_map,
        )
        if rule_suggestion is not None:
            return rule_suggestion
        if not self._is_uncategorized(tx) and self._is_unapproved(tx):
            return self._current_category_suggestion(tx)

        learned = self._build_suggestion(stats_by_payee.get(payee_norm, []), category_name_map)
        if learned is not None:
            return learned
        if default_category_id and default_category_id in category_name_map:
            return {
                "category_id": default_category_id,
                "category_name": category_name_map.get(default_category_id, default_category_id),
                "top_count": 0,
                "total_count": 0,
                "confidence": 0.0,
                "confidence_label": "Default",
                "source": "default",
                "matched_rule_id": None,
            }
        return None

    def get_queue(self, queue_filter_mode: str | None = None) -> dict[str, Any]:
        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        mode = (queue_filter_mode or cfg.queue_filter_mode or QUEUE_FILTER_STRICT).strip()
        if mode not in _VALID_QUEUE_MODES:
            mode = QUEUE_FILTER_STRICT
        show_reconciled = bool(cfg.show_reconciled_transactions)
        queue_limit_enabled = bool(cfg.queue_limit_enabled)
        queue_limit_value = int(cfg.queue_limit_value)
        queue_limit_unit = str(cfg.queue_limit_unit or "days").strip().lower()
        if queue_limit_unit not in _VALID_QUEUE_LIMIT_UNITS:
            queue_limit_unit = "days"
        if queue_limit_value < 1:
            queue_limit_value = 30

        logger.debug(
            (
                "get_queue called mode=%s test_mode_enabled=%s show_reconciled=%s queue_limit_enabled=%s "
                "queue_limit_value=%s queue_limit_unit=%s"
            ),
            mode,
            bool(cfg.test_mode_enabled),
            show_reconciled,
            queue_limit_enabled,
            queue_limit_value,
            queue_limit_unit,
        )
        transactions = self.client.get_transactions_since(None)
        logger.debug(
            "get_queue source transactions=%s starting_balance_skipped=%s",
            len(transactions),
            sum(1 for tx in transactions if self._is_starting_balance(tx)),
        )
        category_groups = self.client.get_categories()
        category_usage_counts = self.ctrl.get_ynab_category_usage_counts(self.budget_id)
        categories = self._categories_payload(category_groups, usage_counts=category_usage_counts)
        category_name_map = self._category_name_by_id(categories)
        custom_rules = self._parse_custom_rules_json(cfg.custom_rules_json)
        default_category_id = str(cfg.default_category_id or "").strip() or None

        filtered = [
            tx
            for tx in transactions
            if self.should_include_for_review(tx, mode)
            and self.should_include_reconciled(tx, show_reconciled)
            and self.should_include_by_limit(
                tx,
                queue_limit_enabled=queue_limit_enabled,
                queue_limit_value=queue_limit_value,
                queue_limit_unit=queue_limit_unit,
            )
        ]
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
            payee_norm = self.normalize_payee(payee_display) or "UNKNOWN"
            needs_category = self._is_uncategorized(tx)
            needs_approval = self._is_unapproved(tx)
            suggestion = self._resolve_transaction_suggestion(
                tx,
                custom_rules=custom_rules,
                payee_norm=payee_norm,
                stats_by_payee=stats_by_payee,
                category_name_map=category_name_map,
                default_category_id=default_category_id,
            )
            resolved_category_id = (
                str(suggestion.get("category_id") or "").strip() if suggestion else ""
            )
            resolved_source = str(suggestion.get("source") or "none") if suggestion else "none"
            group_key = (
                f"{payee_norm}::{resolved_category_id}::{resolved_source}"
                if resolved_category_id
                else payee_norm
            )

            group = grouped.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "payee_normalized": payee_norm,
                    "payee_display": payee_display,
                    "transaction_ids": [],
                    "transactions": [],
                    "latest_date": None,
                    "suggestion": suggestion,
                    "resolved_source": resolved_source,
                    "resolved_category_id": resolved_category_id or None,
                    "resolved_category_name": (
                        str(suggestion.get("category_name") or "") or None if suggestion else None
                    ),
                    "matched_rule_id": (
                        str(suggestion.get("matched_rule_id") or "") or None if suggestion else None
                    ),
                    "needs_category_count": 0,
                    "needs_approval_count": 0,
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
            group["needs_category_count"] += int(needs_category)
            group["needs_approval_count"] += int(needs_approval)
            group["transactions"].append(
                self._queue_transaction_payload(
                    tx,
                    payee_display,
                    needs_category=needs_category,
                    needs_approval=needs_approval,
                    resolved=suggestion,
                )
            )

        groups: list[dict[str, Any]] = []
        for group in grouped.values():
            suggestion = group.get("suggestion")
            confidence_label = suggestion.get("confidence_label") if suggestion else None
            groups.append(
                {
                    "group_key": group["group_key"],
                    "payee_normalized": group["payee_normalized"],
                    "payee_display": group["payee_display"],
                    "transaction_ids": group["transaction_ids"],
                    "transaction_count": len(group["transaction_ids"]),
                    "latest_date": group["latest_date"],
                    "transactions": group["transactions"],
                    "suggestion": suggestion,
                    "confidence_label": confidence_label,
                    "resolved_source": group["resolved_source"],
                    "resolved_category_id": group["resolved_category_id"],
                    "resolved_category_name": group["resolved_category_name"],
                    "matched_rule_id": group["matched_rule_id"],
                    "needs_category_count": group["needs_category_count"],
                    "needs_approval_count": group["needs_approval_count"],
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
            "test_mode_enabled": bool(cfg.test_mode_enabled),
            "show_reconciled_transactions": show_reconciled,
            "queue_limit_enabled": queue_limit_enabled,
            "queue_limit_value": queue_limit_value,
            "queue_limit_unit": queue_limit_unit,
            "quick_apply_include_medium": bool(cfg.quick_apply_include_medium),
            "default_category_id": default_category_id,
            "custom_rules": custom_rules,
            "group_count": len(groups),
            "transaction_count": len(filtered),
            "needs_category_count": sum(1 for tx in filtered if self._is_uncategorized(tx)),
            "needs_approval_count": sum(1 for tx in filtered if self._is_unapproved(tx)),
            "categories": categories,
            "groups": groups,
        }

    def get_approval_queue(self) -> dict[str, Any]:
        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        show_reconciled = bool(cfg.show_reconciled_transactions)
        queue_limit_enabled = bool(cfg.queue_limit_enabled)
        queue_limit_value = int(cfg.queue_limit_value)
        queue_limit_unit = str(cfg.queue_limit_unit or "days").strip().lower()
        if queue_limit_unit not in _VALID_QUEUE_LIMIT_UNITS:
            queue_limit_unit = "days"
        if queue_limit_value < 1:
            queue_limit_value = 30

        logger.debug(
            (
                "get_approval_queue called test_mode_enabled=%s show_reconciled=%s "
                "queue_limit_enabled=%s queue_limit_value=%s queue_limit_unit=%s"
            ),
            bool(cfg.test_mode_enabled),
            show_reconciled,
            queue_limit_enabled,
            queue_limit_value,
            queue_limit_unit,
        )
        transactions = self.client.get_transactions_since(None, transaction_type="unapproved")
        filtered = [
            tx
            for tx in transactions
            if not bool(tx.get("deleted"))
            and self._is_unapproved(tx)
            and self.should_include_reconciled(tx, show_reconciled)
            and self.should_include_by_limit(
                tx,
                queue_limit_enabled=queue_limit_enabled,
                queue_limit_value=queue_limit_value,
                queue_limit_unit=queue_limit_unit,
            )
        ]
        filtered.sort(key=lambda tx: -self._date_sort_value(str(tx.get("date") or "")))
        payload_items = [
            self._queue_transaction_payload(
                tx,
                str(tx.get("payee_name") or "Unknown").strip() or "Unknown",
                needs_category=self._is_uncategorized(tx),
                needs_approval=True,
                resolved=self._current_category_suggestion(tx),
            )
            for tx in filtered
        ]
        logger.debug(
            "get_approval_queue filtered transactions total=%s queue=%s",
            len(transactions),
            len(payload_items),
        )
        return {
            "test_mode_enabled": bool(cfg.test_mode_enabled),
            "show_reconciled_transactions": show_reconciled,
            "queue_limit_enabled": queue_limit_enabled,
            "queue_limit_value": queue_limit_value,
            "queue_limit_unit": queue_limit_unit,
            "transaction_count": len(payload_items),
            "transactions": payload_items,
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

        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        if bool(cfg.test_mode_enabled):
            logger.debug(
                "apply_category test mode simulated tx_count=%s category_id=%s",
                len(tx_ids),
                category_id,
            )
            return {
                "transaction_count": len(tx_ids),
                "approved_count": len(tx_ids),
                "inserted_events": 0,
                "skipped_existing": 0,
                "simulated": True,
                "test_mode_enabled": True,
                "ynab": {
                    "transaction_ids": tx_ids,
                    "simulated": True,
                    "skipped_remote_write": True,
                },
            }

        payload_items = [
            {"id": tx_id, "category_id": category_id, "approved": True} for tx_id in tx_ids
        ]
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
            "approved_count": len(tx_ids),
            "inserted_events": inserted_events,
            "skipped_existing": skipped_existing,
            "simulated": False,
            "test_mode_enabled": False,
            "ynab": ynab_result,
        }

    def approve_transactions(
        self,
        transaction_ids: list[str],
        *,
        approved_by_username: str | None,
    ) -> dict[str, Any]:
        logger.debug(
            "approve_transactions called transaction_count=%s approved_by=%s",
            len(transaction_ids),
            approved_by_username,
        )
        tx_ids = [str(tx_id).strip() for tx_id in transaction_ids if str(tx_id).strip()]
        tx_ids = list(dict.fromkeys(tx_ids))
        if not tx_ids:
            raise ValueError("transaction_ids must not be empty")
        if len(tx_ids) > 200:
            raise ValueError("Max 200 transactions per approve")

        cfg = self.ctrl.get_ynab_categorizer_config(self.budget_id)
        if bool(cfg.test_mode_enabled):
            logger.debug("approve_transactions test mode simulated tx_count=%s", len(tx_ids))
            return {
                "transaction_count": len(tx_ids),
                "approved_count": len(tx_ids),
                "simulated": True,
                "test_mode_enabled": True,
                "ynab": {
                    "transaction_ids": tx_ids,
                    "simulated": True,
                    "skipped_remote_write": True,
                },
            }

        payload_items = [{"id": tx_id, "approved": True} for tx_id in tx_ids]
        ynab_result = self.client.update_transactions_bulk(payload_items)
        logger.debug("approve_transactions completed transaction_count=%s", len(tx_ids))
        return {
            "transaction_count": len(tx_ids),
            "approved_count": len(tx_ids),
            "simulated": False,
            "test_mode_enabled": False,
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
