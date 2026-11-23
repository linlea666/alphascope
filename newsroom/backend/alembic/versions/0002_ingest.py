"""Feed ingestion: source connections, cursors, sync runs, health snapshots, traders, source events, episodes."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE source_connection (
        id serial PRIMARY KEY, name varchar(32) NOT NULL UNIQUE, base_url text NOT NULL,
        contract_schema varchar(16) NOT NULL DEFAULT 'feed.v1', enabled boolean NOT NULL DEFAULT true,
        last_ok_at timestamptz, last_error text, last_error_at timestamptz
    );
    CREATE TABLE ingestion_checkpoint (
        source_id integer NOT NULL REFERENCES source_connection, stream varchar(32) NOT NULL,
        after_id bigint NOT NULL DEFAULT 0, head_id bigint, settled_before timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source_id, stream)
    );
    CREATE TABLE ingestion_checkpoint_history (
        id bigserial PRIMARY KEY, source_id integer NOT NULL REFERENCES source_connection,
        stream varchar(32) NOT NULL, after_id bigint NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_checkpoint_history ON ingestion_checkpoint_history (source_id, stream, recorded_at);
    CREATE TABLE sync_run (
        id bigserial PRIMARY KEY, source_id integer NOT NULL REFERENCES source_connection, stream varchar(32) NOT NULL,
        mode varchar(12) NOT NULL CHECK (mode IN ('incremental', 'replay')),
        started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        status varchar(12) NOT NULL CHECK (status IN ('running', 'success', 'failed')),
        from_id bigint, to_id bigint, pages integer NOT NULL DEFAULT 0, fetched integer NOT NULL DEFAULT 0,
        inserted integer NOT NULL DEFAULT 0, backlog bigint, error text
    );
    CREATE INDEX ix_sync_run_stream ON sync_run (stream, started_at DESC);
    CREATE TABLE source_health_snapshot (
        id bigserial PRIMARY KEY, source_id integer NOT NULL REFERENCES source_connection,
        at timestamptz NOT NULL DEFAULT now(), reachable boolean NOT NULL, payload jsonb, status jsonb NOT NULL
    );
    CREATE INDEX ix_health_at ON source_health_snapshot (source_id, at DESC);
    CREATE TABLE trader (
        id bigserial PRIMARY KEY, trader_key varchar(80) NOT NULL UNIQUE,
        venue varchar(12) NOT NULL CHECK (venue IN ('hyperliquid', 'okx')),
        display_name varchar(128), handle varchar(128), created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        -- a public name only ever comes from an editor-in-chief with a cited source, never from inference
        public_label varchar(128), public_label_source text, public_label_by bigint REFERENCES app_user
    );
    CREATE TABLE trader_context_snapshot (
        id bigserial PRIMARY KEY, trader_id bigint NOT NULL REFERENCES trader,
        fetched_at timestamptz NOT NULL DEFAULT now(), feed_as_of timestamptz, payload jsonb NOT NULL,
        payload_hash char(64) NOT NULL, UNIQUE (trader_id, payload_hash)
    );
    CREATE INDEX ix_context_trader ON trader_context_snapshot (trader_id, fetched_at DESC);
    CREATE TRIGGER trader_context_snapshot_append_only BEFORE UPDATE OR DELETE ON trader_context_snapshot
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    CREATE TABLE position_episode (
        id bigserial PRIMARY KEY, trader_id bigint NOT NULL REFERENCES trader,
        venue varchar(12) NOT NULL, coin varchar(48) NOT NULL,
        direction varchar(5) NOT NULL CHECK (direction IN ('long', 'short')),
        start_kind varchar(16) NOT NULL CHECK (start_kind IN ('open', 'flip', 'first_seen', 'reconstructed')),
        start_precision varchar(8) NOT NULL CHECK (start_precision IN ('exact', 'window', 'unknown')),
        start_from timestamptz, start_to timestamptz NOT NULL,
        status varchar(8) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'flipped')),
        end_from timestamptz, end_to timestamptz, peak_abs_szi numeric NOT NULL DEFAULT 0,
        peak_notional numeric NOT NULL DEFAULT 0, last_szi numeric, last_mark_px numeric, last_entry_px numeric,
        n_increases integer NOT NULL DEFAULT 0, n_decreases integer NOT NULL DEFAULT 0,
        added_notional numeric NOT NULL DEFAULT 0, reduced_notional numeric NOT NULL DEFAULT 0,
        first_source_event_id bigint, last_source_event_id bigint, updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX ux_episode_open ON position_episode (trader_id, coin) WHERE status = 'open';
    CREATE INDEX ix_episode_trader ON position_episode (trader_id, coin, start_to);
    CREATE TABLE source_event (
        id bigserial PRIMARY KEY, source_id integer NOT NULL REFERENCES source_connection,
        source_event_id bigint NOT NULL, trader_id bigint NOT NULL REFERENCES trader,
        venue varchar(12) NOT NULL, coin varchar(48) NOT NULL,
        kind varchar(12) NOT NULL CHECK (kind IN ('open', 'increase', 'decrease', 'close', 'flip', 'first_seen')),
        side varchar(5) NOT NULL, szi_before numeric NOT NULL, szi_after numeric NOT NULL,
        notional_change numeric NOT NULL, mark_px numeric, entry_px numeric, leverage numeric, liq_px numeric,
        event_time timestamptz NOT NULL, time_precision varchar(8) NOT NULL, window_start timestamptz,
        observed_at timestamptz NOT NULL, ingested_at timestamptz NOT NULL DEFAULT now(),
        payload_hash char(64) NOT NULL, episode_id bigint REFERENCES position_episode, processed_at timestamptz,
        UNIQUE (source_id, source_event_id)
    );
    CREATE INDEX ix_source_event_pending ON source_event (id) WHERE processed_at IS NULL;
    CREATE INDEX ix_source_event_trader ON source_event (trader_id, coin, observed_at);
    CREATE INDEX ix_source_event_episode ON source_event (episode_id)""")


def downgrade():
    op.execute("""DROP TABLE source_event; DROP TABLE position_episode; DROP TABLE trader_context_snapshot;
                  DROP TABLE trader; DROP TABLE source_health_snapshot; DROP TABLE sync_run;
                  DROP TABLE ingestion_checkpoint_history; DROP TABLE ingestion_checkpoint;
                  DROP TABLE source_connection""")
