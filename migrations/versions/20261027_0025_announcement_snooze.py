"""Persist deliberate snooze history and idempotency without resetting budgets.

Revision ID: 20261027_0025
Revises: 20261027_0024
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0025"
down_revision = "20261027_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_snooze",
        sa.Column("request_id", sa.Text(), primary_key=True),
        sa.Column(
            "occurrence_id", sa.Text(), sa.ForeignKey("announcement_occurrence.id"), nullable=False
        ),
        sa.Column("snoozed_at", sa.Text(), nullable=False),
        sa.Column("snoozed_by", sa.Text()),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("prior_disposition", sa.Text()),
        sa.Column("prior_due_at", sa.Text(), nullable=False),
        sa.Column("prior_expires_at", sa.Text(), nullable=False),
        sa.Column("due_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.CheckConstraint("minutes IN (5, 10, 15, 30)", name="ck_announcement_snooze_minutes"),
    )


def downgrade():
    op.drop_table("announcement_snooze")
