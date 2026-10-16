"""extend_ynab_categorizer_config_filters

Revision ID: 20260309_0004
Revises: 20260309_0003
Create Date: 2026-03-09 18:20:00.000000

"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "20260309_0004"
down_revision = "20260309_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("migration upgrade %s", revision)
    op.add_column(
        "ynab_categorizer_config",
        sa.Column(
            "show_reconciled_transactions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ynab_categorizer_config",
        sa.Column(
            "queue_limit_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ynab_categorizer_config",
        sa.Column(
            "queue_limit_value",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )
    op.add_column(
        "ynab_categorizer_config",
        sa.Column(
            "queue_limit_unit",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'days'"),
        ),
    )
    op.create_check_constraint(
        "ck_ynab_queue_limit_value",
        "ynab_categorizer_config",
        "queue_limit_value >= 1",
    )
    op.create_check_constraint(
        "ck_ynab_queue_limit_unit",
        "ynab_categorizer_config",
        "queue_limit_unit IN ('days', 'months', 'years')",
    )


def downgrade() -> None:
    logger.debug("migration downgrade %s", revision)
    op.drop_constraint(
        "ck_ynab_queue_limit_unit",
        "ynab_categorizer_config",
        type_="check",
    )
    op.drop_constraint(
        "ck_ynab_queue_limit_value",
        "ynab_categorizer_config",
        type_="check",
    )
    op.drop_column("ynab_categorizer_config", "queue_limit_unit")
    op.drop_column("ynab_categorizer_config", "queue_limit_value")
    op.drop_column("ynab_categorizer_config", "queue_limit_enabled")
    op.drop_column("ynab_categorizer_config", "show_reconciled_transactions")
