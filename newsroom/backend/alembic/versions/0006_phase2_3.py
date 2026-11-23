"""Phase 2-3: column issues as news events, assignment desk fields, scheduling and RPA hand-off, per-assignment
style and model, and the OpenAI Responses protocol."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE ai_model_config DROP CONSTRAINT ai_model_config_protocol_check;
    ALTER TABLE ai_model_config ADD CONSTRAINT ai_model_config_protocol_check
        CHECK (protocol IN ('openai_chat', 'openai_responses', 'anthropic_messages'));
    -- a column issue (weekly scorecard, research piece) is a news event without a single position behind it
    ALTER TABLE news_event ALTER COLUMN trader_id DROP NOT NULL;
    ALTER TABLE news_event ADD COLUMN column_key varchar(32), ADD COLUMN period_key varchar(32),
        ADD COLUMN column_params jsonb;
    CREATE INDEX ix_news_event_column ON news_event (column_key, period_key) WHERE column_key IS NOT NULL;
    ALTER TABLE assignment ADD COLUMN claimed_by bigint REFERENCES app_user, ADD COLUMN claimed_at timestamptz,
        ADD COLUMN due_at timestamptz, ADD COLUMN reviewer_id bigint REFERENCES app_user,
        ADD COLUMN style_key varchar(48), ADD COLUMN model_config_id integer REFERENCES ai_model_config;
    ALTER TABLE editorial_account ADD COLUMN rpa_enabled boolean NOT NULL DEFAULT false;
    ALTER TABLE publication_plan DROP CONSTRAINT publication_plan_status_check;
    ALTER TABLE publication_plan ADD CONSTRAINT publication_plan_status_check
        CHECK (status IN ('planned', 'claimed', 'cancelled', 'done'));
    ALTER TABLE publication_plan ADD COLUMN claimed_at timestamptz, ADD COLUMN note text,
        ADD COLUMN publication_id bigint REFERENCES publication_record;
    CREATE UNIQUE INDEX ux_plan_active ON publication_plan (article_id) WHERE status IN ('planned', 'claimed');
    CREATE INDEX ix_plan_due ON publication_plan (status, planned_at)""")


def downgrade():
    op.execute("""
    DROP INDEX ix_plan_due; DROP INDEX ux_plan_active;
    ALTER TABLE publication_plan DROP COLUMN publication_id, DROP COLUMN note, DROP COLUMN claimed_at;
    UPDATE publication_plan SET status = 'planned' WHERE status = 'claimed';
    ALTER TABLE publication_plan DROP CONSTRAINT publication_plan_status_check;
    ALTER TABLE publication_plan ADD CONSTRAINT publication_plan_status_check
        CHECK (status IN ('planned', 'cancelled', 'done'));
    ALTER TABLE editorial_account DROP COLUMN rpa_enabled;
    ALTER TABLE assignment DROP COLUMN model_config_id, DROP COLUMN style_key, DROP COLUMN reviewer_id,
        DROP COLUMN due_at, DROP COLUMN claimed_at, DROP COLUMN claimed_by;
    DROP INDEX ix_news_event_column;
    ALTER TABLE news_event DROP COLUMN column_params, DROP COLUMN period_key, DROP COLUMN column_key;
    DELETE FROM news_event WHERE trader_id IS NULL;
    ALTER TABLE news_event ALTER COLUMN trader_id SET NOT NULL;
    UPDATE ai_model_config SET protocol = 'openai_chat' WHERE protocol = 'openai_responses';
    ALTER TABLE ai_model_config DROP CONSTRAINT ai_model_config_protocol_check;
    ALTER TABLE ai_model_config ADD CONSTRAINT ai_model_config_protocol_check
        CHECK (protocol IN ('openai_chat', 'anthropic_messages'))""")
