"""Weekly point-in-time test of whether past copy returns persist."""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE copy_persistence_run (
        id bigserial PRIMARY KEY, computed_at timestamptz NOT NULL DEFAULT clock_timestamp(), result jsonb NOT NULL
    )""")


def downgrade():
    op.drop_table("copy_persistence_run")
