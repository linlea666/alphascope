"""sentinels, daily digest, timing null quantiles

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-17 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('timing_score', sa.Column('null_quantiles', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table('zone_sentinel',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('owner', sa.String(length=32), nullable=False, server_default='default'),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('coin', sa.String(length=48), nullable=False),
    sa.Column('side', sa.String(length=5), nullable=False),
    sa.Column('px_min', sa.Numeric(), nullable=False),
    sa.Column('px_max', sa.Numeric(), nullable=False),
    sa.Column('cohort', sa.String(length=16), nullable=False, server_default='all'),
    sa.Column('venue', sa.String(length=16), nullable=False, server_default='all'),
    sa.Column('min_notional', sa.Numeric(), nullable=False, server_default='0'),
    sa.Column('recipients', postgresql.ARRAY(sa.Text()), nullable=False, server_default='{}'),
    sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sentinel_hit',
    sa.Column('sentinel_id', sa.BigInteger(), nullable=False),
    sa.Column('wallet_id', sa.BigInteger(), nullable=False),
    sa.Column('first_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('avg_px', sa.Numeric(), nullable=True),
    sa.Column('notional', sa.Numeric(), nullable=True),
    sa.Column('found_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notify_status', sa.String(length=12), nullable=False, server_default='pending'),
    sa.ForeignKeyConstraint(['sentinel_id'], ['zone_sentinel.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['wallet_id'], ['wallet.id'], ),
    sa.PrimaryKeyConstraint('sentinel_id', 'wallet_id')
    )
    op.create_table('daily_digest',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('day')
    )


def downgrade() -> None:
    op.drop_table('daily_digest')
    op.drop_table('sentinel_hit')
    op.drop_table('zone_sentinel')
    op.drop_column('timing_score', 'null_quantiles')
