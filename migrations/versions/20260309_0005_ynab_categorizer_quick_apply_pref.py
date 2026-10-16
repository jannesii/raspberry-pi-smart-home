"""add_ynab_categorizer_quick_apply_pref

Revision ID: 20260309_0005
Revises: 20260309_0004
Create Date: 2026-03-09 20:10:00.000000

"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = "20260309_0005"
down_revision = "20260309_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    logger.debug("migration upgrade %s", revision)
    op.add_column(
        "ynab_categorizer_config",
        sa.Column(
            "quick_apply_include_medium",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    logger.debug("migration downgrade %s", revision)
    op.drop_column("ynab_categorizer_config", "quick_apply_include_medium")
