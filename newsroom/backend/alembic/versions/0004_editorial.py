"""Editorial desk: styles, accounts and permissions, EIC evaluations, assignments, articles with immutable versions,
media assets, publication plans and records, corrections and manually entered performance."""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None

ARTICLE_STATES = "'drafting', 'draft_ready', 'in_review', 'changes_requested', 'approved', 'published', 'retracted', " \
                 "'killed', 'stale'"


def upgrade():
    op.execute(f"""CREATE TABLE editorial_style (
        id serial PRIMARY KEY, key varchar(48) NOT NULL, version integer NOT NULL,
        language varchar(2) NOT NULL CHECK (language IN ('zh', 'en')), name text NOT NULL, guide text NOT NULL,
        structure jsonb NOT NULL DEFAULT '{{}}', forbidden jsonb NOT NULL DEFAULT '[]', examples jsonb NOT NULL DEFAULT '[]',
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (key, version)
    );
    CREATE TABLE editorial_account (
        id bigserial PRIMARY KEY, handle varchar(64) NOT NULL UNIQUE, display_name text NOT NULL,
        platform varchar(8) NOT NULL DEFAULT 'x', language varchar(2) NOT NULL CHECK (language IN ('zh', 'en')),
        status varchar(8) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'retired')),
        brand varchar(64), positioning text NOT NULL DEFAULT '', audience_profile jsonb NOT NULL DEFAULT '{{}}',
        timezone varchar(48) NOT NULL DEFAULT 'UTC', style_key varchar(48),
        allowed_event_classes text[] NOT NULL DEFAULT '{{}}', min_score numeric NOT NULL DEFAULT 0,
        daily_post_budget integer NOT NULL DEFAULT 5, min_gap_minutes integer NOT NULL DEFAULT 60,
        long_post_allowed boolean NOT NULL DEFAULT false,
        authorization_status varchar(10) NOT NULL DEFAULT 'pending'
            CHECK (authorization_status IN ('pending', 'authorized', 'expired', 'revoked')),
        authorization_expires_at timestamptz, authorization_note text, brand_colors jsonb NOT NULL DEFAULT '{{}}',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE account_permission (
        user_id bigint NOT NULL REFERENCES app_user ON DELETE CASCADE,
        account_id bigint NOT NULL REFERENCES editorial_account ON DELETE CASCADE,
        permission varchar(8) NOT NULL CHECK (permission IN ('view', 'draft', 'review', 'publish', 'manage')),
        PRIMARY KEY (user_id, account_id, permission)
    );
    CREATE TABLE eic_evaluation (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event,
        fact_packet_id bigint NOT NULL REFERENCES fact_packet, ai_call_id bigint, output jsonb NOT NULL,
        decision varchar(12) NOT NULL CHECK (decision IN ('cover', 'hold', 'no_coverage')),
        priority varchar(4) NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_eic_event ON eic_evaluation (news_event_id, created_at DESC);
    CREATE TABLE assignment (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event,
        account_id bigint NOT NULL REFERENCES editorial_account, agent_key varchar(32) NOT NULL DEFAULT 'reporter',
        angle text NOT NULL DEFAULT '', reason text NOT NULL DEFAULT '', format varchar(16) NOT NULL DEFAULT 'single_post',
        source varchar(8) NOT NULL CHECK (source IN ('eic_ai', 'editor')), eic_evaluation_id bigint REFERENCES eic_evaluation,
        status varchar(10) NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed', 'accepted', 'rejected', 'drafting', 'done', 'cancelled')),
        needs_human boolean NOT NULL DEFAULT false, decided_by bigint REFERENCES app_user, decided_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX ux_assignment_active ON assignment (news_event_id, account_id)
        WHERE status NOT IN ('rejected', 'cancelled');
    CREATE TABLE article (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event, story_id bigint REFERENCES news_story,
        account_id bigint NOT NULL REFERENCES editorial_account, assignment_id bigint REFERENCES assignment,
        language varchar(2) NOT NULL CHECK (language IN ('zh', 'en')),
        status varchar(18) NOT NULL DEFAULT 'drafting' CHECK (status IN ({ARTICLE_STATES})),
        current_version_id bigint, stale_reason text, duplicate_ack_by bigint REFERENCES app_user,
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_article_account ON article (account_id, status);
    CREATE INDEX ix_article_event ON article (news_event_id);
    CREATE TABLE article_version (
        id bigserial PRIMARY KEY, article_id bigint NOT NULL REFERENCES article, version_no integer NOT NULL,
        parent_version_id bigint REFERENCES article_version, origin varchar(5) NOT NULL CHECK (origin IN ('ai', 'human')),
        author_user_id bigint REFERENCES app_user, ai_call_id bigint, agent_key varchar(32), prompt_ref varchar(64),
        model varchar(128), fact_packet_id bigint NOT NULL REFERENCES fact_packet, fact_packet_version integer NOT NULL,
        headline text NOT NULL DEFAULT '', alt_headlines jsonb NOT NULL DEFAULT '[]', body text NOT NULL,
        thread jsonb, angle text NOT NULL DEFAULT '', added_value text NOT NULL DEFAULT '',
        difference_from_others text NOT NULL DEFAULT '', why_shareable text NOT NULL DEFAULT '',
        risks jsonb NOT NULL DEFAULT '[]', facts_used text[] NOT NULL DEFAULT '{{}}', text_hash char(64) NOT NULL,
        guard_status varchar(4) NOT NULL CHECK (guard_status IN ('pass', 'warn', 'fail')), guard_report jsonb NOT NULL,
        guard_version varchar(8) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (article_id, version_no)
    );
    CREATE TRIGGER article_version_append_only BEFORE UPDATE OR DELETE ON article_version
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    ALTER TABLE article ADD CONSTRAINT fk_article_current_version FOREIGN KEY (current_version_id) REFERENCES article_version;
    CREATE TABLE article_fact_reference (
        article_version_id bigint NOT NULL REFERENCES article_version, fact_packet_id bigint NOT NULL REFERENCES fact_packet,
        fact_id varchar(96) NOT NULL, PRIMARY KEY (article_version_id, fact_id)
    );
    CREATE INDEX ix_fact_reference_packet ON article_fact_reference (fact_packet_id, fact_id);
    CREATE TABLE media_asset (
        id bigserial PRIMARY KEY, kind varchar(10) NOT NULL CHECK (kind IN ('card_svg', 'card_png')),
        template_key varchar(48) NOT NULL, template_version varchar(8) NOT NULL,
        fact_packet_id bigint NOT NULL REFERENCES fact_packet, language varchar(2) NOT NULL, brand varchar(64) NOT NULL,
        data_hash char(64) NOT NULL, file_path text NOT NULL, sha256 char(64) NOT NULL, width integer, height integer,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (template_key, template_version, fact_packet_id, language, brand, kind)
    );
    CREATE TABLE publication_plan (
        id bigserial PRIMARY KEY, article_id bigint NOT NULL REFERENCES article,
        account_id bigint NOT NULL REFERENCES editorial_account, planned_at timestamptz NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'cancelled', 'done')),
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE publication_record (
        id bigserial PRIMARY KEY, article_id bigint NOT NULL REFERENCES article,
        article_version_id bigint NOT NULL REFERENCES article_version,
        account_id bigint NOT NULL REFERENCES editorial_account,
        status varchar(10) NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'retracted')),
        x_post_id varchar(32), x_url text, published_at timestamptz NOT NULL,
        recorded_by bigint NOT NULL REFERENCES app_user, recorded_at timestamptz NOT NULL DEFAULT now(),
        final_text text NOT NULL, final_text_hash char(64) NOT NULL, matches_version boolean NOT NULL,
        guard_report jsonb NOT NULL, asset_ids bigint[] NOT NULL DEFAULT '{{}}',
        retracted_at timestamptz, retracted_by bigint REFERENCES app_user, retract_reason text,
        UNIQUE (account_id, x_post_id)
    );
    CREATE INDEX ix_publication_account ON publication_record (account_id, published_at DESC);
    CREATE TABLE correction_record (
        id bigserial PRIMARY KEY, news_event_id bigint NOT NULL REFERENCES news_event,
        old_fact_packet_id bigint REFERENCES fact_packet, new_fact_packet_id bigint NOT NULL REFERENCES fact_packet,
        material boolean NOT NULL, changed_facts jsonb NOT NULL DEFAULT '[]', affected jsonb NOT NULL DEFAULT '{{}}',
        status varchar(12) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'corrected', 'no_action')),
        handled_by bigint REFERENCES app_user, handled_at timestamptz, note text,
        detected_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE performance_snapshot (
        id bigserial PRIMARY KEY, publication_id bigint NOT NULL REFERENCES publication_record,
        captured_at timestamptz NOT NULL, impressions bigint, likes bigint, reposts bigint, quotes bigint,
        replies bigint, bookmarks bigint, profile_visits bigint, follows bigint, link_clicks bigint,
        source varchar(8) NOT NULL CHECK (source IN ('manual', 'csv', 'api')), entered_by bigint REFERENCES app_user,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_performance_publication ON performance_snapshot (publication_id, captured_at)""")


def downgrade():
    op.execute("""DROP TABLE performance_snapshot; DROP TABLE correction_record; DROP TABLE publication_record;
                  DROP TABLE publication_plan; DROP TABLE media_asset; DROP TABLE article_fact_reference;
                  ALTER TABLE article DROP CONSTRAINT fk_article_current_version; DROP TABLE article_version;
                  DROP TABLE article; DROP TABLE assignment; DROP TABLE eic_evaluation; DROP TABLE account_permission;
                  DROP TABLE editorial_account; DROP TABLE editorial_style""")
