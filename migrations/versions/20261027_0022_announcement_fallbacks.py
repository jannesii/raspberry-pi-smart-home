"""Discord fallback notifications for reminder occurrences.

One logical fallback per occurrence (unique ``occurrence_id``) with its own
status and attempt budget; each webhook request is a numbered attempt row
persisted before the request is sent, so restarts neither reset the budget nor
resend a request whose acceptance is unknown.

Revision ID: 20261027_0022
Revises: 20261027_0021
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0022"
down_revision = "20261027_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_fallback",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.Text(),
            sa.ForeignKey("announcement_occurrence.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_reason", sa.Text()),
        sa.Column("attempts_made", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'accepted', 'unknown', 'failed', 'unavailable')",
            name="ck_announcement_fallback_status",
        ),
        sa.CheckConstraint(
            "reason IN ('failed', 'partial', 'unknown', 'expired_undelivered')",
            name="ck_announcement_fallback_reason",
        ),
        sa.CheckConstraint(
            "attempts_made BETWEEN 0 AND max_attempts",
            name="ck_announcement_fallback_attempts",
        ),
    )
    op.create_index(
        "idx_announcement_fallback_status",
        "announcement_fallback",
        ["status", "next_attempt_at"],
    )
    op.create_table(
        "announcement_fallback_attempt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "fallback_id",
            sa.Text(),
            sa.ForeignKey("announcement_fallback.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("retry_after_s", sa.Float()),
        sa.Column("detail", sa.Text()),
        sa.Column("intent_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
        sa.UniqueConstraint(
            "fallback_id", "number", name="uq_announcement_fallback_attempt_number"
        ),
        sa.CheckConstraint(
            "status IN ('sending', 'accepted', 'rejected', 'rate_limited', 'failed', "
            "'not_configured', 'unknown')",
            name="ck_announcement_fallback_attempt_status",
        ),
    )


def downgrade():
    op.drop_table("announcement_fallback_attempt")
    op.drop_index("idx_announcement_fallback_status", table_name="announcement_fallback")
    op.drop_table("announcement_fallback")
