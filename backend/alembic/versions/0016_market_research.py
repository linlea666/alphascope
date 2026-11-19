"""Immutable market research evidence and event definitions."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE market_snapshot ADD COLUMN available_at TIMESTAMPTZ")
    op.execute("""
CREATE TABLE research_event (
    id BIGSERIAL NOT NULL,
    event_key VARCHAR(96) NOT NULL,
    revision INTEGER NOT NULL,
    coin VARCHAR(32) NOT NULL,
    title VARCHAR(160) NOT NULL,
    direction VARCHAR(8) NOT NULL,
    cluster_key VARCHAR(96) NOT NULL,
    source VARCHAR(32) NOT NULL,
    method VARCHAR(48) NOT NULL,
    start_at TIMESTAMP WITH TIME ZONE NOT NULL,
    end_at TIMESTAMP WITH TIME ZONE NOT NULL,
    start_px NUMERIC NOT NULL,
    end_px NUMERIC NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (event_key, revision)
)
""")
    op.execute("CREATE INDEX ix_research_event_coin_start ON research_event (coin, start_at)")
    op.execute("""
CREATE TABLE research_experiment (
    id BIGSERIAL NOT NULL,
    coin VARCHAR(32) NOT NULL,
    hypothesis VARCHAR(48) NOT NULL,
    version INTEGER NOT NULL,
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
    specification JSONB NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (coin, hypothesis, version)
)
""")
    op.execute("""
CREATE TABLE research_frame (
    id BIGSERIAL NOT NULL,
    coin VARCHAR(32) NOT NULL,
    venue VARCHAR(16) NOT NULL,
    bucket_ts TIMESTAMP WITH TIME ZONE NOT NULL,
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    available_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
    version VARCHAR(16) NOT NULL,
    metrics JSONB NOT NULL,
    accounts JSONB NOT NULL,
    context JSONB NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (coin, venue, bucket_ts, version)
)
""")
    op.execute("CREATE INDEX ix_research_frame_available ON research_frame (coin, venue, available_at)")
    op.execute("""
CREATE TABLE research_evaluation (
    id BIGSERIAL NOT NULL,
    experiment_id BIGINT NOT NULL,
    cutoff TIMESTAMP WITH TIME ZONE NOT NULL,
    result JSONB NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (experiment_id, cutoff),
    FOREIGN KEY(experiment_id) REFERENCES research_experiment (id)
)
""")
    op.execute("""
CREATE TABLE research_forecast (
    id BIGSERIAL NOT NULL,
    coin VARCHAR(32) NOT NULL,
    model VARCHAR(32) NOT NULL,
    horizon INTEGER NOT NULL,
    issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
    cutoff TIMESTAMP WITH TIME ZONE NOT NULL,
    due_at TIMESTAMP WITH TIME ZONE NOT NULL,
    frame_id BIGINT NOT NULL,
    evidence JSONB NOT NULL,
    outcome JSONB,
    PRIMARY KEY (id),
    UNIQUE (frame_id, model, horizon),
    FOREIGN KEY(frame_id) REFERENCES research_frame (id)
)
""")
    op.execute("CREATE INDEX ix_forecast_due ON research_forecast (coin, due_at)")
    op.execute("""
CREATE TABLE research_notice (
    id BIGSERIAL NOT NULL,
    frame_id BIGINT NOT NULL,
    kind VARCHAR(40) NOT NULL,
    evidence JSONB NOT NULL,
    due_at TIMESTAMP WITH TIME ZONE NOT NULL,
    outcome JSONB,
    watching BOOLEAN DEFAULT false NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (frame_id, kind),
    FOREIGN KEY(frame_id) REFERENCES research_frame (id)
)
""")
    op.execute("CREATE INDEX ix_research_notice_frame_id ON research_notice (frame_id)")


def downgrade():
    op.drop_table("research_notice")
    op.drop_table("research_forecast")
    op.drop_table("research_evaluation")
    op.drop_table("research_frame")
    op.drop_table("research_experiment")
    op.drop_table("research_event")
    op.drop_column("market_snapshot", "available_at")
