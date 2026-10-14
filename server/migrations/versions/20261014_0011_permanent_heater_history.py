"""Remove the known heater status retention trigger without deleting records.

Revision ID: 20261014_0011
Revises: 20261013_0010
"""

from alembic import op

revision = "20261014_0011"
down_revision = "20261013_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS cleanup_car_heater_status_after_insert")
    elif dialect == "postgresql":
        # PostgreSQL has no repository-defined retention trigger. Remove the
        # known legacy name if deployed there; leave shared functions intact.
        op.execute(
            "DROP TRIGGER IF EXISTS cleanup_car_heater_status_after_insert ON car_heater_status"
        )
    else:
        raise RuntimeError("Heater history migration supports SQLite and PostgreSQL only")


def downgrade() -> None:
    # Intentionally irreversible: rollback must not silently resume deletion of
    # winter evidence. No schema or records were removed by this revision.
    pass
