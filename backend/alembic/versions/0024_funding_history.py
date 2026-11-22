"""Verified monthly funding settlements for the overview."""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE funding_archive_month (
        venue text NOT NULL, coin text NOT NULL, month date NOT NULL,
        source text NOT NULL, collected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        settlements jsonb NOT NULL, PRIMARY KEY (venue, coin, month)
    )""")


def downgrade():
    op.drop_table("funding_archive_month")
