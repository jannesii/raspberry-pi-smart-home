"""Per-member evidence for announcements through a Cast speaker group.

A group target plays one synchronized stream; the group's own playback report
stays the only playback evidence. ``member_evidence`` records what is known
about each configured member (reachable, listed by the group, dropped out
during playback) so partial delivery can name the affected speaker without
claiming audibility. Nullable: single-speaker attempts leave it empty.

Revision ID: 20261027_0023
Revises: 20261027_0022
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0023"
down_revision = "20261027_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("announcement_attempt", sa.Column("member_evidence", sa.Text()))


def downgrade():
    # SQLite needs batch mode (table copy) to drop a column.
    with op.batch_alter_table("announcement_attempt") as batch:
        batch.drop_column("member_evidence")
