from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import (
    YnabApplyEvent,
    YnabBootstrapState,
    YnabCategorizerConfig,
    YnabPayeeCategoryStat,
)
from ..schema import (
    ynab_apply_events,
    ynab_bootstrap_state,
    ynab_categorizer_config,
    ynab_payee_category_stats,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


_VALID_QUEUE_MODES = {"strict", "all_uncategorized", "skip_transfers"}
_VALID_QUEUE_LIMIT_UNITS = {"days", "months", "years"}


class YnabCategorizerMixin:
    def _ynab_require_sa_engine(self) -> Engine:
        sa_engine: Engine | None = self._sa_engine
        if sa_engine is None:
            raise RuntimeError("SQLAlchemy engine not initialized")
        return sa_engine

    def _ynab_now_iso(self) -> str:
        return datetime.now(self.finland_tz).isoformat()  # type: ignore[attr-defined]

    def get_ynab_categorizer_config(self, budget_id: str) -> YnabCategorizerConfig:
        logger.debug("get_ynab_categorizer_config called budget_id=%s", budget_id)
        sa_engine = self._ynab_require_sa_engine()
        stmt = select(ynab_categorizer_config).where(
            ynab_categorizer_config.c.budget_id == budget_id
        )
        with sa_engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()

        if row is None:
            cfg = YnabCategorizerConfig(
                id=1,
                budget_id=budget_id,
                queue_filter_mode="strict",
                show_reconciled_transactions=False,
                queue_limit_enabled=False,
                queue_limit_value=30,
                queue_limit_unit="days",
                quick_apply_include_medium=False,
                default_category_id=None,
                custom_rules_json=None,
                updated_ts=self._ynab_now_iso(),
            )
            logger.debug("get_ynab_categorizer_config returning default config=%s", cfg)
            return cfg

        cfg = YnabCategorizerConfig(
            id=int(row["id"]),
            budget_id=str(row["budget_id"]),
            queue_filter_mode=str(row["queue_filter_mode"]),
            show_reconciled_transactions=bool(row.get("show_reconciled_transactions")),
            queue_limit_enabled=bool(row.get("queue_limit_enabled")),
            queue_limit_value=int(row.get("queue_limit_value") or 30),
            queue_limit_unit=str(row.get("queue_limit_unit") or "days"),
            quick_apply_include_medium=bool(row.get("quick_apply_include_medium")),
            default_category_id=(
                str(row.get("default_category_id")) if row.get("default_category_id") else None
            ),
            custom_rules_json=(
                str(row.get("custom_rules_json")) if row.get("custom_rules_json") else None
            ),
            updated_ts=str(row["updated_ts"]),
        )
        logger.debug("get_ynab_categorizer_config loaded config=%s", cfg)
        return cfg

    def save_ynab_categorizer_config(
        self,
        budget_id: str,
        queue_filter_mode: str,
        *,
        show_reconciled_transactions: bool | None = None,
        queue_limit_enabled: bool | None = None,
        queue_limit_value: int | None = None,
        queue_limit_unit: str | None = None,
        quick_apply_include_medium: bool | None = None,
        default_category_id: str | None = None,
        custom_rules_json: str | None = None,
    ) -> YnabCategorizerConfig:
        logger.debug(
            (
                "save_ynab_categorizer_config called budget_id=%s queue_filter_mode=%s "
                "show_reconciled_transactions=%s queue_limit_enabled=%s "
                "queue_limit_value=%s queue_limit_unit=%s quick_apply_include_medium=%s "
                "default_category_id=%s custom_rules_json_set=%s"
            ),
            budget_id,
            queue_filter_mode,
            show_reconciled_transactions,
            queue_limit_enabled,
            queue_limit_value,
            queue_limit_unit,
            quick_apply_include_medium,
            default_category_id,
            custom_rules_json is not None,
        )
        mode = (queue_filter_mode or "").strip()
        if mode not in _VALID_QUEUE_MODES:
            raise ValueError(f"Invalid queue_filter_mode: {queue_filter_mode}")
        show_reconciled = bool(show_reconciled_transactions)
        limit_enabled = bool(queue_limit_enabled)
        limit_value = int(queue_limit_value) if queue_limit_value is not None else 30
        limit_unit_value = str(queue_limit_unit or "days").strip().lower()
        quick_apply_include_medium_value = bool(quick_apply_include_medium)
        default_category_id_value = str(default_category_id or "").strip() or None
        custom_rules_json_value = str(custom_rules_json).strip() if custom_rules_json else None
        if limit_value < 1:
            raise ValueError(f"Invalid queue_limit_value: {queue_limit_value}")
        if limit_unit_value not in _VALID_QUEUE_LIMIT_UNITS:
            raise ValueError(f"Invalid queue_limit_unit: {queue_limit_unit}")

        sa_engine = self._ynab_require_sa_engine()
        now = self._ynab_now_iso()

        stmt = (
            pg_insert(ynab_categorizer_config)
            .values(
                id=1,
                budget_id=budget_id,
                queue_filter_mode=mode,
                show_reconciled_transactions=show_reconciled,
                queue_limit_enabled=limit_enabled,
                queue_limit_value=limit_value,
                queue_limit_unit=limit_unit_value,
                quick_apply_include_medium=quick_apply_include_medium_value,
                default_category_id=default_category_id_value,
                custom_rules_json=custom_rules_json_value,
                updated_ts=now,
            )
            .on_conflict_do_update(
                index_elements=[ynab_categorizer_config.c.id],
                set_={
                    "budget_id": budget_id,
                    "queue_filter_mode": mode,
                    "show_reconciled_transactions": show_reconciled,
                    "queue_limit_enabled": limit_enabled,
                    "queue_limit_value": limit_value,
                    "queue_limit_unit": limit_unit_value,
                    "quick_apply_include_medium": quick_apply_include_medium_value,
                    "default_category_id": default_category_id_value,
                    "custom_rules_json": custom_rules_json_value,
                    "updated_ts": now,
                },
            )
        )
        if sa_engine.dialect.name == "sqlite":
            stmt = (
                sqlite_insert(ynab_categorizer_config)
                .values(
                    id=1,
                    budget_id=budget_id,
                    queue_filter_mode=mode,
                    show_reconciled_transactions=show_reconciled,
                    queue_limit_enabled=limit_enabled,
                    queue_limit_value=limit_value,
                    queue_limit_unit=limit_unit_value,
                    quick_apply_include_medium=quick_apply_include_medium_value,
                    default_category_id=default_category_id_value,
                    custom_rules_json=custom_rules_json_value,
                    updated_ts=now,
                )
                .on_conflict_do_update(
                    index_elements=[ynab_categorizer_config.c.id],
                    set_={
                        "budget_id": budget_id,
                        "queue_filter_mode": mode,
                        "show_reconciled_transactions": show_reconciled,
                        "queue_limit_enabled": limit_enabled,
                        "queue_limit_value": limit_value,
                        "queue_limit_unit": limit_unit_value,
                        "quick_apply_include_medium": quick_apply_include_medium_value,
                        "default_category_id": default_category_id_value,
                        "custom_rules_json": custom_rules_json_value,
                        "updated_ts": now,
                    },
                )
            )

        with sa_engine.begin() as conn:
            conn.execute(stmt)

        cfg = YnabCategorizerConfig(
            id=1,
            budget_id=budget_id,
            queue_filter_mode=mode,
            show_reconciled_transactions=show_reconciled,
            queue_limit_enabled=limit_enabled,
            queue_limit_value=limit_value,
            queue_limit_unit=limit_unit_value,
            quick_apply_include_medium=quick_apply_include_medium_value,
            default_category_id=default_category_id_value,
            custom_rules_json=custom_rules_json_value,
            updated_ts=now,
        )
        logger.debug("save_ynab_categorizer_config saved config=%s", cfg)
        return cfg

    def get_ynab_bootstrap_state(self, budget_id: str) -> YnabBootstrapState | None:
        logger.debug("get_ynab_bootstrap_state called budget_id=%s", budget_id)
        sa_engine = self._ynab_require_sa_engine()
        stmt = select(ynab_bootstrap_state).where(ynab_bootstrap_state.c.budget_id == budget_id)
        with sa_engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()

        if row is None:
            logger.debug("get_ynab_bootstrap_state no state for budget_id=%s", budget_id)
            return None

        state = YnabBootstrapState(
            budget_id=str(row["budget_id"]),
            bootstrapped_at=str(row["bootstrapped_at"]),
            history_start_date=str(row["history_start_date"]),
            history_end_date=str(row["history_end_date"]),
        )
        logger.debug("get_ynab_bootstrap_state loaded state=%s", state)
        return state

    def save_ynab_bootstrap_state(self, state: YnabBootstrapState) -> None:
        logger.debug("save_ynab_bootstrap_state called state=%s", state)
        sa_engine = self._ynab_require_sa_engine()

        stmt = (
            pg_insert(ynab_bootstrap_state)
            .values(
                budget_id=state.budget_id,
                bootstrapped_at=state.bootstrapped_at,
                history_start_date=state.history_start_date,
                history_end_date=state.history_end_date,
            )
            .on_conflict_do_update(
                index_elements=[ynab_bootstrap_state.c.budget_id],
                set_={
                    "bootstrapped_at": state.bootstrapped_at,
                    "history_start_date": state.history_start_date,
                    "history_end_date": state.history_end_date,
                },
            )
        )
        if sa_engine.dialect.name == "sqlite":
            stmt = (
                sqlite_insert(ynab_bootstrap_state)
                .values(
                    budget_id=state.budget_id,
                    bootstrapped_at=state.bootstrapped_at,
                    history_start_date=state.history_start_date,
                    history_end_date=state.history_end_date,
                )
                .on_conflict_do_update(
                    index_elements=[ynab_bootstrap_state.c.budget_id],
                    set_={
                        "bootstrapped_at": state.bootstrapped_at,
                        "history_start_date": state.history_start_date,
                        "history_end_date": state.history_end_date,
                    },
                )
            )

        with sa_engine.begin() as conn:
            conn.execute(stmt)
        logger.debug("save_ynab_bootstrap_state persisted budget_id=%s", state.budget_id)

    def get_ynab_stats_for_payees(
        self,
        budget_id: str,
        payee_normalized_values: list[str],
    ) -> dict[str, list[YnabPayeeCategoryStat]]:
        logger.debug(
            "get_ynab_stats_for_payees called budget_id=%s payee_count=%s",
            budget_id,
            len(payee_normalized_values),
        )
        if not payee_normalized_values:
            return {}

        sa_engine = self._ynab_require_sa_engine()
        stmt = select(ynab_payee_category_stats).where(
            ynab_payee_category_stats.c.budget_id == budget_id,
            ynab_payee_category_stats.c.payee_normalized.in_(payee_normalized_values),
        )

        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        grouped: dict[str, list[YnabPayeeCategoryStat]] = defaultdict(list)
        for row in rows:
            stat = YnabPayeeCategoryStat(
                id=int(row["id"]),
                budget_id=str(row["budget_id"]),
                payee_normalized=str(row["payee_normalized"]),
                category_id=str(row["category_id"]),
                count=int(row["count"]),
                last_used_at=str(row["last_used_at"]) if row["last_used_at"] is not None else None,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            grouped[stat.payee_normalized].append(stat)

        logger.debug(
            "get_ynab_stats_for_payees returning payee_groups=%s total_rows=%s",
            len(grouped),
            len(rows),
        )
        return dict(grouped)

    def get_ynab_category_usage_counts(self, budget_id: str) -> dict[str, int]:
        logger.debug("get_ynab_category_usage_counts called budget_id=%s", budget_id)
        sa_engine = self._ynab_require_sa_engine()
        stmt = (
            select(
                ynab_payee_category_stats.c.category_id,
                func.sum(ynab_payee_category_stats.c.count).label("total_count"),
            )
            .where(ynab_payee_category_stats.c.budget_id == budget_id)
            .group_by(ynab_payee_category_stats.c.category_id)
        )
        with sa_engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        counts = {
            str(row["category_id"]): int(row["total_count"] or 0)
            for row in rows
            if row.get("category_id") is not None
        }
        logger.debug(
            "get_ynab_category_usage_counts returning categories=%s",
            len(counts),
        )
        return counts

    def increment_ynab_payee_category_stat(
        self,
        budget_id: str,
        payee_normalized: str,
        category_id: str,
        *,
        last_used_at: str | None = None,
    ) -> None:
        logger.debug(
            "increment_ynab_payee_category_stat called budget_id=%s payee=%s category=%s",
            budget_id,
            payee_normalized,
            category_id,
        )
        sa_engine = self._ynab_require_sa_engine()
        now = self._ynab_now_iso()
        used_ts = last_used_at or now

        stmt = (
            pg_insert(ynab_payee_category_stats)
            .values(
                budget_id=budget_id,
                payee_normalized=payee_normalized,
                category_id=category_id,
                count=1,
                last_used_at=used_ts,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    ynab_payee_category_stats.c.budget_id,
                    ynab_payee_category_stats.c.payee_normalized,
                    ynab_payee_category_stats.c.category_id,
                ],
                set_={
                    "count": ynab_payee_category_stats.c.count + 1,
                    "last_used_at": used_ts,
                    "updated_at": now,
                },
            )
        )
        if sa_engine.dialect.name == "sqlite":
            stmt = (
                sqlite_insert(ynab_payee_category_stats)
                .values(
                    budget_id=budget_id,
                    payee_normalized=payee_normalized,
                    category_id=category_id,
                    count=1,
                    last_used_at=used_ts,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[
                        ynab_payee_category_stats.c.budget_id,
                        ynab_payee_category_stats.c.payee_normalized,
                        ynab_payee_category_stats.c.category_id,
                    ],
                    set_={
                        "count": ynab_payee_category_stats.c.count + 1,
                        "last_used_at": used_ts,
                        "updated_at": now,
                    },
                )
            )

        with sa_engine.begin() as conn:
            conn.execute(stmt)

        logger.debug(
            "increment_ynab_payee_category_stat persisted budget_id=%s payee=%s category=%s",
            budget_id,
            payee_normalized,
            category_id,
        )

    def replace_ynab_payee_category_stats(
        self,
        budget_id: str,
        stats: list[YnabPayeeCategoryStat],
    ) -> None:
        logger.debug(
            "replace_ynab_payee_category_stats called budget_id=%s rows=%s",
            budget_id,
            len(stats),
        )
        sa_engine = self._ynab_require_sa_engine()

        with sa_engine.begin() as conn:
            conn.execute(
                delete(ynab_payee_category_stats).where(
                    ynab_payee_category_stats.c.budget_id == budget_id
                )
            )
            if stats:
                payload = [
                    {
                        "budget_id": s.budget_id,
                        "payee_normalized": s.payee_normalized,
                        "category_id": s.category_id,
                        "count": int(s.count),
                        "last_used_at": s.last_used_at,
                        "created_at": s.created_at,
                        "updated_at": s.updated_at,
                    }
                    for s in stats
                ]
                conn.execute(insert(ynab_payee_category_stats), payload)

        logger.debug(
            "replace_ynab_payee_category_stats completed budget_id=%s inserted=%s",
            budget_id,
            len(stats),
        )

    def has_ynab_apply_event(self, budget_id: str, transaction_id: str) -> bool:
        logger.debug(
            "has_ynab_apply_event called budget_id=%s transaction_id=%s",
            budget_id,
            transaction_id,
        )
        sa_engine = self._ynab_require_sa_engine()
        stmt = select(ynab_apply_events.c.id).where(
            ynab_apply_events.c.budget_id == budget_id,
            ynab_apply_events.c.transaction_id == transaction_id,
        )
        with sa_engine.connect() as conn:
            row = conn.execute(stmt).first()
        found = row is not None
        logger.debug(
            "has_ynab_apply_event result budget_id=%s transaction_id=%s found=%s",
            budget_id,
            transaction_id,
            found,
        )
        return found

    def record_ynab_apply_event(self, event: YnabApplyEvent) -> bool:
        logger.debug("record_ynab_apply_event called event=%s", event)
        sa_engine = self._ynab_require_sa_engine()

        stmt = (
            pg_insert(ynab_apply_events)
            .values(
                budget_id=event.budget_id,
                transaction_id=event.transaction_id,
                payee_normalized=event.payee_normalized,
                category_id=event.category_id,
                applied_by_username=event.applied_by_username,
                applied_at=event.applied_at,
            )
            .on_conflict_do_nothing(
                index_elements=[ynab_apply_events.c.budget_id, ynab_apply_events.c.transaction_id]
            )
        )
        if sa_engine.dialect.name == "sqlite":
            stmt = (
                sqlite_insert(ynab_apply_events)
                .values(
                    budget_id=event.budget_id,
                    transaction_id=event.transaction_id,
                    payee_normalized=event.payee_normalized,
                    category_id=event.category_id,
                    applied_by_username=event.applied_by_username,
                    applied_at=event.applied_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ynab_apply_events.c.budget_id,
                        ynab_apply_events.c.transaction_id,
                    ]
                )
            )

        with sa_engine.begin() as conn:
            result = conn.execute(stmt)

        inserted = (result.rowcount or 0) > 0
        logger.debug(
            "record_ynab_apply_event completed transaction_id=%s inserted=%s",
            event.transaction_id,
            inserted,
        )
        return inserted
