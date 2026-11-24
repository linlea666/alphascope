"""The squares and the stars (docs/16): Binance as a venue (lead traders via the kolcue relay), Binance Square as a
platform, how an account names traders, the star list, daily board snapshots, the relay's positions and closes, a
persona per trader, duels between traders, and predictions a story makes and comes back to."""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE trader DROP CONSTRAINT trader_venue_check;
    ALTER TABLE trader ADD CONSTRAINT trader_venue_check CHECK (venue IN ('hyperliquid', 'okx', 'binance'));
    ALTER TABLE editorial_account DROP CONSTRAINT editorial_account_platform_check;
    ALTER TABLE editorial_account ADD CONSTRAINT editorial_account_platform_check
        CHECK (platform IN ('x', 'okx_square', 'binance_square'));
    ALTER TABLE assignment DROP CONSTRAINT assignment_source_check;
    ALTER TABLE assignment ADD CONSTRAINT assignment_source_check CHECK (source IN ('eic_ai', 'editor', 'trial', 'star'));
    ALTER TABLE editorial_account ADD COLUMN name_display varchar(8) NOT NULL DEFAULT 'nickname'
        CHECK (name_display IN ('nickname', 'masked'));
    -- OKX Square tells stories about people now: the seed's data-explainer default becomes the character story
    UPDATE editorial_account SET style_key = 'zh_square_story'
        WHERE platform = 'okx_square' AND style_key = 'zh_data_explainer';

    CREATE TABLE star_trader (
        id bigserial PRIMARY KEY, trader_key varchar(80) NOT NULL UNIQUE,
        venue varchar(12) NOT NULL CHECK (venue IN ('hyperliquid', 'okx', 'binance')), name text NOT NULL,
        note text NOT NULL DEFAULT '', active boolean NOT NULL DEFAULT true, min_notional_usd numeric,
        -- the latest account figures the source reports for this star (Binance: relay snapshot and 30-day stats)
        equity numeric, snapshot_at timestamptz, stats_30d jsonb, stats_at timestamptz,
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now()
    );

    -- one row per trader, board and day: the trajectory a story's "back then" comes from
    CREATE TABLE trader_board_snapshot (
        trader_key varchar(80) NOT NULL, board varchar(4) NOT NULL, business_date date NOT NULL, rank integer NOT NULL,
        pnl numeric, pnl_ratio numeric, asset numeric, off_peak numeric, name text,
        PRIMARY KEY (trader_key, board, business_date)
    );
    CREATE INDEX ix_board_snapshot_day ON trader_board_snapshot (board, business_date);

    -- the relay's view of each star's open positions and closed ones (kolcue-relay/1)
    CREATE TABLE relay_position (
        trader_key varchar(80) NOT NULL, position_id varchar(160) NOT NULL, symbol varchar(48) NOT NULL,
        side varchar(5) NOT NULL, size numeric NOT NULL, entry_px numeric, mark_px numeric, upl numeric, lever numeric,
        notional numeric, margin numeric, opened_at timestamptz, first_seen_at timestamptz NOT NULL,
        last_seen_at timestamptz NOT NULL, open boolean NOT NULL DEFAULT true,
        PRIMARY KEY (trader_key, position_id)
    );
    CREATE TABLE relay_close (
        trader_key varchar(80) NOT NULL, position_id varchar(160) NOT NULL, symbol varchar(48) NOT NULL,
        side varchar(5) NOT NULL, opened_at timestamptz, closed_at timestamptz NOT NULL, open_px numeric,
        close_px numeric, size numeric, lever numeric, margin numeric, pnl numeric, full_close boolean NOT NULL,
        seen_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (trader_key, position_id, closed_at)
    );
    CREATE INDEX ix_relay_close_time ON relay_close (trader_key, closed_at);

    CREATE TABLE trader_persona (
        trader_key varchar(80) PRIMARY KEY, venue varchar(12) NOT NULL, name text NOT NULL,
        tags jsonb NOT NULL DEFAULT '[]', stage varchar(12) NOT NULL DEFAULT 'quiet'
            CHECK (stage IN ('rising', 'streak', 'peak', 'crisis', 'fall', 'comeback', 'quiet')),
        stage_since timestamptz, prev_stage varchar(12), traits jsonb NOT NULL DEFAULT '{}',
        editor_note text NOT NULL DEFAULT '', edited_by bigint REFERENCES app_user, updated_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE star_duel (
        id bigserial PRIMARY KEY, coin varchar(48) NOT NULL, a_key varchar(80) NOT NULL, a_side varchar(5) NOT NULL,
        b_key varchar(80) NOT NULL, b_side varchar(5) NOT NULL, opened_at timestamptz NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'void')),
        result jsonb, resolved_at timestamptz, UNIQUE (coin, a_key, b_key, opened_at)
    );

    CREATE TABLE story_prediction (
        id bigserial PRIMARY KEY, story_id bigint REFERENCES news_story, article_id bigint REFERENCES article,
        trader_key varchar(80), question text NOT NULL,
        kind varchar(16) NOT NULL CHECK (kind IN ('price_above', 'price_below', 'position_closed', 'by_time')),
        params jsonb NOT NULL DEFAULT '{}', due_at timestamptz NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'expired')),
        outcome jsonb, resolved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_prediction_open ON story_prediction (due_at) WHERE status = 'open'""")


def downgrade():
    op.execute("""
    DROP TABLE story_prediction; DROP TABLE star_duel; DROP TABLE trader_persona; DROP TABLE relay_close;
    DROP TABLE relay_position; DROP TABLE trader_board_snapshot; DROP TABLE star_trader;
    ALTER TABLE editorial_account DROP COLUMN name_display;
    UPDATE assignment SET source = 'editor' WHERE source = 'star';
    ALTER TABLE assignment DROP CONSTRAINT assignment_source_check;
    ALTER TABLE assignment ADD CONSTRAINT assignment_source_check CHECK (source IN ('eic_ai', 'editor', 'trial'));
    DELETE FROM editorial_account WHERE platform = 'binance_square';
    ALTER TABLE editorial_account DROP CONSTRAINT editorial_account_platform_check;
    ALTER TABLE editorial_account ADD CONSTRAINT editorial_account_platform_check CHECK (platform IN ('x', 'okx_square'));
    DELETE FROM trader WHERE venue = 'binance';
    ALTER TABLE trader DROP CONSTRAINT trader_venue_check;
    ALTER TABLE trader ADD CONSTRAINT trader_venue_check CHECK (venue IN ('hyperliquid', 'okx'))""")
