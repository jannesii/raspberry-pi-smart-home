"""add test mode toggle to ynab categorizer config

Revision ID: 20260330_0008
Revises: 20260324_0007
Create Date: 2026-03-30 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260330_0008"
down_revision = "20260324_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ynab_categorizer_config",
        sa.Column("test_mode_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ynab_categorizer_config", "test_mode_enabled")
