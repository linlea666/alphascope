"""Publishing platforms beyond X (OKX Square) and the venues each account covers: X accounts cover Hyperliquid, whose
data is fully public; OKX material goes to OKX Square accounts."""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE editorial_account ALTER COLUMN platform TYPE varchar(16);
    ALTER TABLE editorial_account ADD CONSTRAINT editorial_account_platform_check CHECK (platform IN ('x', 'okx_square'));
    ALTER TABLE editorial_account ADD COLUMN venues text[] NOT NULL DEFAULT '{}';
    UPDATE editorial_account SET venues = '{hyperliquid}' WHERE platform = 'x'""")


def downgrade():
    op.execute("""
    ALTER TABLE editorial_account DROP COLUMN venues;
    ALTER TABLE editorial_account DROP CONSTRAINT editorial_account_platform_check;
    UPDATE editorial_account SET platform = 'x' WHERE platform <> 'x';
    ALTER TABLE editorial_account ALTER COLUMN platform TYPE varchar(8)""")
