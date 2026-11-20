"""Discover public Binance profile IDs and retain previous manual priorities."""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE binance_profile_directory (
        profile_id varchar(19) PRIMARY KEY, name text, pinned boolean NOT NULL DEFAULT false,
        first_seen timestamptz NOT NULL DEFAULT now(), btc_seen timestamptz, eth_seen timestamptz,
        attempted timestamptz, priority_risk boolean NOT NULL DEFAULT false
    )""")
    op.create_index("ix_binance_snapshot_recent", "binance_profile_snapshot", ["observed_at"])
    op.execute("""INSERT INTO binance_profile_directory(profile_id, pinned, attempted)
        SELECT pid, true, (cfg.value->'attempts'->>pid)::timestamptz FROM app_setting cfg,
        LATERAL jsonb_array_elements_text(cfg.value->'ids') AS legacy(pid) WHERE cfg.key='binance_profiles'
        AND pid ~ '^[0-9]{19}$' ON CONFLICT DO NOTHING""")


def downgrade():
    op.drop_index("ix_binance_snapshot_recent", table_name="binance_profile_snapshot")
    op.execute("""UPDATE app_setting SET value=jsonb_set(value,'{ids}',coalesce(
        (SELECT jsonb_agg(profile_id) FROM (SELECT profile_id FROM binance_profile_directory
        WHERE pinned ORDER BY first_seen LIMIT 10) p),'[]'::jsonb)) WHERE key='binance_profiles'""")
    op.drop_table("binance_profile_directory")
