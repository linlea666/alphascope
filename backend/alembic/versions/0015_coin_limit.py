"""coin_limit: exchange maximum leverage per coin

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-18 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('coin_limit',
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('coin', sa.String(length=48), nullable=False),
    sa.Column('max_leverage', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('venue', 'coin')
    )


def downgrade() -> None:
    op.drop_table('coin_limit')
