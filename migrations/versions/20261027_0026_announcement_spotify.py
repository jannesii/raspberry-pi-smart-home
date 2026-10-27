"""Optional encrypted Spotify connection and explicit target mapping.

Revision ID: 20261027_0026
Revises: 20261027_0024
"""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0026"
down_revision = "20261027_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_spotify",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("devices", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_announcement_spotify_singleton"),
    )


def downgrade():
    op.drop_table("announcement_spotify")
