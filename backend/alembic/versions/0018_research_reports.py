"""Frozen event reports, including source vintage and calculation version."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE research_report (
          id bigserial PRIMARY KEY,
          event_id bigint NOT NULL REFERENCES research_event(id),
          coin varchar(32) NOT NULL,
          hours integer NOT NULL,
          mode varchar(24) NOT NULL,
          vintage timestamptz NOT NULL,
          source_version varchar(32) NOT NULL,
          published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          payload jsonb NOT NULL,
          UNIQUE(event_id, coin, hours, mode, vintage, source_version)
        )
    """)


def downgrade():
    op.drop_table("research_report")
