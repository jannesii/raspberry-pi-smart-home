"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa


logger = logging.getLogger(__name__)

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    logger.debug("migration upgrade %s", revision)
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    logger.debug("migration downgrade %s", revision)
    ${downgrades if downgrades else "pass"}
