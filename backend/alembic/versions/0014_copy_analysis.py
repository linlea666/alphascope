"""copy analysis: isolated sim positions, MAE, copy backtests, copyability score, trader health

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-17 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('copy_sim', sa.Column('leverage_mode', sa.String(length=8), nullable=False, server_default='account'))
    for col in ('leverage', 'liq_px', 'margin', 'worst_upnl', 'best_upnl'):
        op.add_column('sim_position', sa.Column(col, sa.Numeric(), nullable=True))
    op.add_column('sim_trade', sa.Column('mae', sa.Numeric(), nullable=True))
    op.create_table('copy_backtest',
    sa.Column('wallet_id', sa.BigInteger(), nullable=False),
    sa.Column('window_days', sa.Integer(), nullable=False),
    sa.Column('params_key', sa.String(length=32), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('capital', sa.Numeric(), nullable=False),
    sa.Column('ret', sa.Numeric(), nullable=True),
    sa.Column('pnl', sa.Numeric(), nullable=True),
    sa.Column('mdd', sa.Numeric(), nullable=True),
    sa.Column('leader_ret', sa.Numeric(), nullable=True),
    sa.Column('retention', sa.Numeric(), nullable=True),
    sa.Column('trades', sa.Integer(), nullable=False),
    sa.Column('liquidations', sa.Integer(), nullable=False),
    sa.Column('costs', sa.Numeric(), nullable=False),
    sa.Column('n_decisions', sa.Integer(), nullable=False),
    sa.Column('approx_equity', sa.Boolean(), nullable=False),
    sa.Column('delay_curve', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('curve', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['wallet_id'], ['wallet.id'], ),
    sa.PrimaryKeyConstraint('wallet_id', 'window_days', 'params_key')
    )
    op.create_index('ix_copy_backtest_rank', 'copy_backtest', ['window_days', 'params_key', 'ret'], unique=False)
    op.create_table('copy_score',
    sa.Column('wallet_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('score', sa.Numeric(), nullable=True),
    sa.Column('grade', sa.String(length=2), nullable=True),
    sa.Column('components', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['wallet_id'], ['wallet.id'], ),
    sa.PrimaryKeyConstraint('wallet_id')
    )
    op.create_table('trader_health',
    sa.Column('wallet_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('prev_status', sa.String(length=12), nullable=True),
    sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['wallet_id'], ['wallet.id'], ),
    sa.PrimaryKeyConstraint('wallet_id')
    )


def downgrade() -> None:
    op.drop_table('trader_health')
    op.drop_table('copy_score')
    op.drop_index('ix_copy_backtest_rank', table_name='copy_backtest')
    op.drop_table('copy_backtest')
    op.drop_column('sim_trade', 'mae')
    for col in ('best_upnl', 'worst_upnl', 'margin', 'liq_px', 'leverage'):
        op.drop_column('sim_position', col)
    op.drop_column('copy_sim', 'leverage_mode')
