"""News events, their state machine and history, evidence, versioned fact packets and stories."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None

# kept identical to nr.events.states.TRANSITIONS (a test compares them)
TRANSITIONS = [
    ("detected", "candidate"), ("detected", "blocked"), ("detected", "dismissed"),
    ("blocked", "candidate"), ("blocked", "dismissed"), ("blocked", "archived"),
    ("candidate", "blocked"), ("candidate", "evaluating"), ("candidate", "assigned"), ("candidate", "declined"),
    ("candidate", "dismissed"), ("candidate", "archived"),
    ("evaluating", "evaluated"), ("evaluating", "candidate"), ("evaluating", "blocked"),
    ("evaluated", "assigned"), ("evaluated", "declined"), ("evaluated", "candidate"), ("evaluated", "blocked"),
    ("evaluated", "archived"),
    ("assigned", "covered"), ("assigned", "blocked"), ("assigned", "declined"), ("assigned", "archived"),
    ("covered", "archived"), ("declined", "candidate"), ("declined", "archived"),
    ("dismissed", "candidate"), ("dismissed", "archived"),
]
STATES = "'detected', 'blocked', 'candidate', 'dismissed', 'evaluating', 'evaluated', 'assigned', 'declined', " \
         "'covered', 'archived'"


def upgrade():
    op.execute(f"""CREATE TABLE news_story (
        id bigserial PRIMARY KEY, story_key varchar(128) NOT NULL UNIQUE,
        kind varchar(16) NOT NULL CHECK (kind IN ('trader_saga', 'coin_theme')), trader_id bigint REFERENCES trader,
        status varchar(8) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dormant', 'closed')),
        title_zh text, title_en text, opened_at timestamptz NOT NULL DEFAULT now(), last_event_at timestamptz,
        closed_at timestamptz
    );
    CREATE TABLE news_event (
        id bigserial PRIMARY KEY, dedupe_key char(64) NOT NULL UNIQUE, event_class varchar(32) NOT NULL,
        rule_version varchar(8) NOT NULL, trader_id bigint NOT NULL REFERENCES trader,
        episode_id bigint REFERENCES position_episode, venue varchar(12) NOT NULL, coin varchar(48) NOT NULL,
        direction varchar(5), escalation_level smallint NOT NULL DEFAULT 0,
        liquidation_status varchar(10) NOT NULL DEFAULT 'none' CHECK (liquidation_status IN ('none', 'suspected', 'confirmed')),
        state varchar(12) NOT NULL DEFAULT 'detected' CHECK (state IN ({STATES})),
        state_changed_at timestamptz NOT NULL DEFAULT now(), score numeric, score_breakdown jsonb, score_version varchar(8),
        gate_status varchar(8) NOT NULL DEFAULT 'pending' CHECK (gate_status IN ('pending', 'pass', 'blocked')),
        blocking_issues jsonb NOT NULL DEFAULT '[]', current_fact_packet_id bigint, current_fact_version integer,
        story_id bigint REFERENCES news_story, priority varchar(4),
        first_source_event_id bigint REFERENCES source_event, last_source_event_id bigint REFERENCES source_event,
        source_event_count integer NOT NULL DEFAULT 1, detected_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(), facts_refreshed_at timestamptz
    );
    CREATE INDEX ix_news_event_state ON news_event (state, detected_at DESC);
    CREATE INDEX ix_news_event_trader ON news_event (trader_id, coin);
    CREATE INDEX ix_news_event_episode ON news_event (episode_id, event_class);
    CREATE INDEX ix_news_event_story ON news_event (story_id);
    CREATE TABLE event_state_transition (
        from_state varchar(12) NOT NULL, to_state varchar(12) NOT NULL, PRIMARY KEY (from_state, to_state)
    );
    CREATE TABLE event_state_history (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event,
        from_state varchar(12), to_state varchar(12) NOT NULL, at timestamptz NOT NULL DEFAULT clock_timestamp(),
        actor_kind varchar(8) NOT NULL CHECK (actor_kind IN ('user', 'system', 'ai')),
        actor_user_id bigint REFERENCES app_user, reason text
    );
    CREATE INDEX ix_event_state_history ON event_state_history (news_event_id, at);
    CREATE TABLE event_evidence (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event,
        kind varchar(20) NOT NULL CHECK (kind IN ('source_event', 'context_snapshot', 'health_snapshot', 'at_risk')),
        ref_id bigint NOT NULL, content_hash char(64), captured_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (news_event_id, kind, ref_id)
    );
    CREATE TABLE fact_packet (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event, version integer NOT NULL,
        schema varchar(8) NOT NULL DEFAULT 'fp.v1', builder_version varchar(16) NOT NULL,
        built_at timestamptz NOT NULL DEFAULT now(), content jsonb NOT NULL, content_hash char(64) NOT NULL,
        supersedes_id bigint REFERENCES fact_packet, change_summary jsonb, material boolean NOT NULL DEFAULT true,
        gate_status varchar(8) NOT NULL CHECK (gate_status IN ('pass', 'blocked')),
        blocking_issues jsonb NOT NULL DEFAULT '[]', UNIQUE (news_event_id, version)
    );
    CREATE TRIGGER fact_packet_append_only BEFORE UPDATE OR DELETE ON fact_packet
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    ALTER TABLE news_event ADD CONSTRAINT fk_news_event_packet FOREIGN KEY (current_fact_packet_id) REFERENCES fact_packet;
    CREATE TABLE story_event (
        story_id bigint NOT NULL REFERENCES news_story, news_event_id bigint NOT NULL REFERENCES news_event,
        seq integer NOT NULL, role varchar(12) NOT NULL CHECK (role IN ('origin', 'follow_up', 'resolution')),
        pinned_fact_packet_id bigint REFERENCES fact_packet, added_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (story_id, news_event_id), UNIQUE (story_id, seq)
    )""")
    op.execute("INSERT INTO event_state_transition VALUES " + ", ".join(f"('{a}', '{b}')" for a, b in TRANSITIONS))


def downgrade():
    op.execute("""DROP TABLE story_event; ALTER TABLE news_event DROP CONSTRAINT fk_news_event_packet;
                  DROP TABLE fact_packet; DROP TABLE event_evidence; DROP TABLE event_state_history;
                  DROP TABLE event_state_transition; DROP TABLE news_event; DROP TABLE news_story""")
