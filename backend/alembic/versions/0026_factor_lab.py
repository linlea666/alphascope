"""Binance USDT-M 5-minute positioning metrics and the nightly historical factor screen."""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = depends_on = None


def upgrade():
    # One row per 5-minute stat from data.binance.vision daily metrics files (checksummed, in market_file).
    op.execute("""CREATE TABLE binance_metric (
        coin text NOT NULL, t timestamptz NOT NULL, oi numeric, oi_usd numeric, top_account_ratio float,
        top_position_ratio float, account_ratio float, taker_ratio float, PRIMARY KEY (coin, t)
    )""")
    op.execute("""CREATE TABLE factor_lab_run (
        id bigserial PRIMARY KEY, computed_at timestamptz NOT NULL DEFAULT clock_timestamp(), version text NOT NULL,
        result jsonb NOT NULL
    )""")


def downgrade():
    op.drop_table("factor_lab_run")
    op.drop_table("binance_metric")
