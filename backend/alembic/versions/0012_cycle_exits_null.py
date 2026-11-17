"""cycle exits null: OKX open cycles stored JSON null instead of an empty array

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17 10:00:00.000000
"""
from alembic import op


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE position_cycle SET exits = '[]'::jsonb WHERE jsonb_typeof(exits) = 'null'")
    op.execute("UPDATE position_cycle SET entries = '[]'::jsonb WHERE jsonb_typeof(entries) = 'null'")


def downgrade() -> None:
    pass
