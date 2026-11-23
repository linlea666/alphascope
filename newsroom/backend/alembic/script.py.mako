"""${message}"""
from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = depends_on = None


def upgrade():
    ${upgrades if upgrades else "pass"}


def downgrade():
    ${downgrades if downgrades else "pass"}
