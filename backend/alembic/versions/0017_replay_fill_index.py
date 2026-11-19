"""Time window scans for market-wide event attribution."""

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        invalid = op.get_bind().scalar(
            sa.text("""SELECT NOT indisvalid FROM pg_index
            WHERE indexrelid=to_regclass('ix_fill_coin_time')""")
        )
        if invalid:
            op.execute("DROP INDEX CONCURRENTLY ix_fill_coin_time")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_fill_coin_time ON fill (coin,time)")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_fill_coin_time")
