"""add_ynab_categorizer_tables

Revision ID: 20260309_0003
Revises: b5ebed63e22c
Create Date: 2026-03-09 12:00:00.000000

"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "20260309_0003"
down_revision = "b5ebed63e22c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("migration upgrade %s", revision)

    op.create_table(
        "ynab_payee_category_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("budget_id", sa.Text(), nullable=False),
        sa.Column("payee_normalized", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "budget_id",
            "payee_normalized",
            "category_id",
            name="uq_ynab_payee_category_stats",
        ),
    )

    op.create_table(
        "ynab_apply_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("budget_id", sa.Text(), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("payee_normalized", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Text(), nullable=False),
        sa.Column("applied_by_username", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("budget_id", "transaction_id", name="uq_ynab_apply_events"),
    )

    op.create_table(
        "ynab_bootstrap_state",
        sa.Column("budget_id", sa.Text(), primary_key=True),
        sa.Column("bootstrapped_at", sa.Text(), nullable=False),
        sa.Column("history_start_date", sa.Text(), nullable=False),
        sa.Column("history_end_date", sa.Text(), nullable=False),
    )

    op.create_table(
        "ynab_categorizer_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("budget_id", sa.Text(), nullable=False),
        sa.Column(
            "queue_filter_mode", sa.Text(), nullable=False, server_default=sa.text("'strict'")
        ),
        sa.Column("updated_ts", sa.Text(), nullable=False),
        sa.UniqueConstraint("budget_id", name="uq_ynab_categorizer_config_budget"),
        sa.CheckConstraint("id = 1", name="ck_ynab_categorizer_config_singleton"),
    )

    op.create_index(
        "idx_ynab_stats_budget_payee",
        "ynab_payee_category_stats",
        ["budget_id", "payee_normalized"],
    )
    op.create_index(
        "idx_ynab_stats_budget_category",
        "ynab_payee_category_stats",
        ["budget_id", "category_id"],
    )


def downgrade() -> None:
    logger.debug("migration downgrade %s", revision)

    op.drop_index("idx_ynab_stats_budget_category", table_name="ynab_payee_category_stats")
    op.drop_index("idx_ynab_stats_budget_payee", table_name="ynab_payee_category_stats")

    op.drop_table("ynab_categorizer_config")
    op.drop_table("ynab_bootstrap_state")
    op.drop_table("ynab_apply_events")
    op.drop_table("ynab_payee_category_stats")
