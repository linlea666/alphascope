"""binance klines source, symbol map, position cycles and decisions

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # market_candle gains a source dimension; existing rows came from Hyperliquid.
    op.add_column("market_candle", sa.Column("source", sa.String(16), nullable=False, server_default="hyperliquid"))
    op.alter_column("market_candle", "source", server_default=None)
    op.drop_constraint("market_candle_pkey", "market_candle", type_="primary")
    op.create_primary_key("market_candle_pkey", "market_candle", ["source", "coin", "interval", "t"])

    op.create_table(
        "market_file",
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "path"),
    )
    op.create_table(
        "market_symbol",
        sa.Column("coin", sa.String(48), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("multiplier", sa.Numeric(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("median_ratio", sa.Numeric(), nullable=True),
        sa.Column("basis_p95_bps", sa.Numeric(), nullable=True),
        sa.Column("n_samples", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("coin", "source"),
    )
    op.add_column("fill_sync", sa.Column("reconstructed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fill_sync", sa.Column("recon_breaks", sa.Integer(), nullable=True))

    op.create_table(
        "position_cycle",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_id", sa.BigInteger(), sa.ForeignKey("wallet.id"), nullable=False),
        sa.Column("coin", sa.String(48), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("scale", sa.String(8), nullable=False),
        sa.Column("holding_hours", sa.Numeric(), nullable=False),
        sa.Column("entry_px", sa.Numeric(), nullable=True),
        sa.Column("exit_px", sa.Numeric(), nullable=True),
        sa.Column("max_size", sa.Numeric(), nullable=False),
        sa.Column("max_notional", sa.Numeric(), nullable=False),
        sa.Column("n_fills", sa.Integer(), nullable=False),
        sa.Column("n_decisions", sa.Integer(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False),
        sa.Column("fees", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_position_cycle_wallet", "position_cycle", ["wallet_id", "coin", "opened_at"])
    op.create_index("ix_position_cycle_scale", "position_cycle", ["scale", "closed_at"])
    op.create_table(
        "trade_decision",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_id", sa.BigInteger(), sa.ForeignKey("wallet.id"), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), sa.ForeignKey("position_cycle.id", ondelete="CASCADE"), nullable=True),
        sa.Column("coin", sa.String(48), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_fills", sa.Integer(), nullable=False),
        sa.Column("size", sa.Numeric(), nullable=False),
        sa.Column("notional", sa.Numeric(), nullable=False),
        sa.Column("vwap", sa.Numeric(), nullable=False),
        sa.Column("pos_before", sa.Numeric(), nullable=False),
        sa.Column("pos_after", sa.Numeric(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(), nullable=False),
        sa.Column("fee", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_decision_wallet", "trade_decision", ["wallet_id", "coin", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_trade_decision_wallet", table_name="trade_decision")
    op.drop_table("trade_decision")
    op.drop_index("ix_position_cycle_scale", table_name="position_cycle")
    op.drop_index("ix_position_cycle_wallet", table_name="position_cycle")
    op.drop_table("position_cycle")
    op.drop_column("fill_sync", "recon_breaks")
    op.drop_column("fill_sync", "reconstructed_at")
    op.drop_table("market_symbol")
    op.drop_table("market_file")
    op.execute("DELETE FROM market_candle WHERE source <> 'hyperliquid'")
    op.drop_constraint("market_candle_pkey", "market_candle", type_="primary")
    op.create_primary_key("market_candle_pkey", "market_candle", ["coin", "interval", "t"])
    op.drop_column("market_candle", "source")
