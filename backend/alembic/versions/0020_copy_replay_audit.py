"""Record every historical cache re-evaluation at its original cutoff."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE copy_replay_audit (
          wallet_id bigint NOT NULL REFERENCES wallet(id),
          window_days integer NOT NULL,
          source_cutoff timestamptz NOT NULL,
          version varchar(32) NOT NULL,
          outcome jsonb NOT NULL,
          published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(wallet_id,window_days,source_cutoff,version))
    """)


def downgrade():
    op.drop_table("copy_replay_audit")
