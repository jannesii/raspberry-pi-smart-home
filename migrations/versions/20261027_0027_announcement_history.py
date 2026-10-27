"""Identity-only deduplication after deliberate history deletion."""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0027"
down_revision = "20261027_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_consumed",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("request_id", sa.Text(), unique=True),
        sa.Column("schedule_id", sa.Text()),
        sa.Column("instance_due_at", sa.Text()),
        sa.UniqueConstraint(
            "schedule_id", "instance_due_at", name="uq_announcement_consumed_instance"
        ),
    )


def downgrade():
    op.drop_table("announcement_consumed")
