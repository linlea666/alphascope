"""Freeze five-minute public exchange observations for the research desk."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE exchange_observation (
          id bigserial PRIMARY KEY,
          coin text NOT NULL,
          bucket timestamptz NOT NULL,
          published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          payload jsonb NOT NULL,
          watching boolean NOT NULL DEFAULT false,
          UNIQUE(coin,bucket)
        )
    """)
    op.create_index("ix_exchange_publication", "exchange_observation", ["coin", "published_at"])


def downgrade():
    op.drop_table("exchange_observation")
