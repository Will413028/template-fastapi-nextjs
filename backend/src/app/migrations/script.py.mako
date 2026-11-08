"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}


# Forward only, no downgrade(): a rollback is the previous image on the new schema (docs/rules/database-design.md).
def upgrade() -> None:
    ${upgrades if upgrades else "pass"}
