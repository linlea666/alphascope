"""Archive successful, explicitly shared Binance profile observations."""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE binance_profile_snapshot (
            id bigserial PRIMARY KEY, profile_id varchar(19) NOT NULL,
            observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            shared boolean NOT NULL, positions jsonb NOT NULL
        )
    """)
    op.create_index("ix_binance_profile_observed", "binance_profile_snapshot", ["profile_id", "observed_at"])


def downgrade():
    op.drop_table("binance_profile_snapshot")
