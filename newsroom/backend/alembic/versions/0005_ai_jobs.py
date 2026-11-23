"""AI models, versioned prompt templates and agent profiles, the call log, and the DB-backed job queue."""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE ai_model_config (
        id serial PRIMARY KEY, name varchar(64) NOT NULL UNIQUE,
        protocol varchar(20) NOT NULL CHECK (protocol IN ('openai_chat', 'anthropic_messages')),
        base_url text NOT NULL, model varchar(128) NOT NULL, api_key_secret text,
        auth_header varchar(10) NOT NULL DEFAULT 'bearer' CHECK (auth_header IN ('bearer', 'x-api-key')),
        anthropic_version varchar(16) NOT NULL DEFAULT '2023-06-01',
        json_mode varchar(12) NOT NULL DEFAULT 'json_object' CHECK (json_mode IN ('none', 'json_object', 'json_schema')),
        extra_body jsonb NOT NULL DEFAULT '{}', timeout_s integer NOT NULL DEFAULT 90,
        max_retries integer NOT NULL DEFAULT 2, max_concurrency integer NOT NULL DEFAULT 2,
        input_usd_per_mtok numeric NOT NULL DEFAULT 0, output_usd_per_mtok numeric NOT NULL DEFAULT 0,
        enabled boolean NOT NULL DEFAULT true, last_test_at timestamptz, last_test_ok boolean,
        last_test_detail jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE prompt_template (
        id serial PRIMARY KEY, key varchar(48) NOT NULL, version integer NOT NULL, system_text text NOT NULL,
        user_template text NOT NULL, sha256 char(64) NOT NULL, notes text, created_by bigint REFERENCES app_user,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (key, version)
    );
    CREATE TRIGGER prompt_template_append_only BEFORE UPDATE OR DELETE ON prompt_template
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    CREATE TABLE ai_agent_profile (
        id serial PRIMARY KEY, agent_key varchar(32) NOT NULL, version integer NOT NULL, display_name text NOT NULL,
        role_description text NOT NULL, model_config_id integer REFERENCES ai_model_config,
        fallback_model_config_id integer REFERENCES ai_model_config, prompt_key varchar(48) NOT NULL,
        prompt_version integer NOT NULL, max_output_tokens integer NOT NULL DEFAULT 2000,
        budget_usd_per_task numeric NOT NULL DEFAULT 0.5, active boolean NOT NULL DEFAULT true,
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (agent_key, version)
    );
    CREATE UNIQUE INDEX ux_agent_active ON ai_agent_profile (agent_key) WHERE active;
    CREATE TABLE job (
        id bigserial PRIMARY KEY, kind varchar(32) NOT NULL, payload jsonb NOT NULL DEFAULT '{}', dedupe_key varchar(128),
        status varchar(10) NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'done', 'failed', 'dead', 'cancelled')),
        priority integer NOT NULL DEFAULT 100, run_after timestamptz NOT NULL DEFAULT now(),
        attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 4, locked_by varchar(64),
        lease_until timestamptz, last_error text, result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz
    );
    CREATE UNIQUE INDEX ux_job_dedupe ON job (dedupe_key) WHERE status IN ('queued', 'running');
    CREATE INDEX ix_job_ready ON job (status, run_after, priority) WHERE status = 'queued';
    CREATE TABLE ai_call_log (
        id bigserial PRIMARY KEY, job_id bigint REFERENCES job, agent_key varchar(32), prompt_ref varchar(64),
        model_config_id integer REFERENCES ai_model_config, protocol varchar(20), model varchar(128),
        purpose varchar(32) NOT NULL, news_event_id bigint, article_id bigint, attempt integer NOT NULL DEFAULT 1,
        fallback_used boolean NOT NULL DEFAULT false, request_hash char(64), input_tokens integer, output_tokens integer,
        cost_usd numeric NOT NULL DEFAULT 0, latency_ms integer,
        status varchar(16) NOT NULL CHECK (status IN ('ok', 'http_error', 'timeout', 'refused', 'truncated',
            'invalid_json', 'schema_invalid', 'semantic_invalid', 'budget_blocked', 'disabled')),
        error text, raw_output text, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_ai_call_created ON ai_call_log (created_at);
    CREATE INDEX ix_ai_call_job ON ai_call_log (job_id)""")


def downgrade():
    op.execute("""DROP TABLE ai_call_log; DROP TABLE job; DROP TABLE ai_agent_profile; DROP TABLE prompt_template;
                  DROP TABLE ai_model_config""")
