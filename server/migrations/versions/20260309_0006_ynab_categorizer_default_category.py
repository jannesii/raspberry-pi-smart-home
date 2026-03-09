"""add default category to ynab categorizer config

Revision ID: 20260309_0006
Revises: 20260309_0005
Create Date: 2026-03-09 14:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260309_0006"
down_revision = "20260309_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ynab_categorizer_config",
        sa.Column("default_category_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ynab_categorizer_config", "default_category_id")
