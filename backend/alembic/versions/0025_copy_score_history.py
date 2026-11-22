"""Frozen daily copyability grades and what copying each graded trader returned afterwards."""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE copy_score_history (
        wallet_id bigint NOT NULL REFERENCES wallet(id), as_of date NOT NULL, computed_at timestamptz NOT NULL,
        version text NOT NULL, status varchar(16) NOT NULL, score numeric, grade varchar(2), components jsonb NOT NULL,
        PRIMARY KEY (wallet_id, as_of)
    )""")
    op.execute("CREATE INDEX ix_copy_score_history_day ON copy_score_history (as_of) WHERE status = 'scored'")
    # The only grades that exist so far are the current ones; they start the record.
    op.execute("""INSERT INTO copy_score_history (wallet_id, as_of, computed_at, version, status, score, grade, components)
        SELECT wallet_id, (computed_at AT TIME ZONE 'UTC')::date, computed_at, 'c1', status, score, grade, components
        FROM copy_score""")
    op.execute("""CREATE TABLE copy_score_outcome (
        wallet_id bigint NOT NULL, as_of date NOT NULL, horizon_days int NOT NULL, since timestamptz NOT NULL,
        until timestamptz NOT NULL, ret numeric, leader_ret numeric, trades int, reason text,
        computed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (wallet_id, as_of, horizon_days),
        FOREIGN KEY (wallet_id, as_of) REFERENCES copy_score_history (wallet_id, as_of) ON DELETE CASCADE
    )""")


def downgrade():
    op.drop_table("copy_score_outcome")
    op.drop_table("copy_score_history")
