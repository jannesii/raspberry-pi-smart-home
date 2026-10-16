"""add custom rules json to ynab categorizer config

Revision ID: 20260324_0007
Revises: 20260309_0006
Create Date: 2026-03-24 20:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260324_0007"
down_revision = "20260309_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ynab_categorizer_config",
        sa.Column("custom_rules_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ynab_categorizer_config", "custom_rules_json")
