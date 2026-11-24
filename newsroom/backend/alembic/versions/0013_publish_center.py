"""Publishing: drafts expire with their news, the full wallet address travels as the post's reply, every publication
records its channel, an AI desk reviewer leaves a verdict per version, and named API tokens let RPA and browser
extensions take ready posts and report back."""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None

STATES = ("'drafting', 'draft_ready', 'in_review', 'changes_requested', 'approved', 'published', 'retracted', "
          "'killed', 'stale'")


def upgrade():
    op.execute(f"""
    ALTER TABLE article DROP CONSTRAINT article_status_check;
    ALTER TABLE article ADD CONSTRAINT article_status_check CHECK (status IN ({STATES}, 'expired'));
    ALTER TABLE article ADD COLUMN expires_at timestamptz, ADD COLUMN claimed_by_token bigint, ADD COLUMN claimed_at timestamptz;
    CREATE INDEX ix_article_expiry ON article (expires_at) WHERE status IN
        ('drafting', 'draft_ready', 'in_review', 'changes_requested', 'approved', 'stale');
    ALTER TABLE article_version ADD COLUMN style_key varchar(64), ADD COLUMN reply_text text;
    ALTER TABLE editorial_account ADD COLUMN reply_explorer_link boolean NOT NULL DEFAULT false;
    ALTER TABLE publication_record ADD COLUMN channel varchar(10) NOT NULL DEFAULT 'manual'
        CHECK (channel IN ('manual', 'rpa', 'extension', 'api')), ADD COLUMN reply_url text,
        ADD COLUMN api_token_id bigint;
    CREATE TABLE article_review (
        id bigserial PRIMARY KEY, article_version_id bigint NOT NULL REFERENCES article_version,
        verdict varchar(8) NOT NULL CHECK (verdict IN ('approve', 'revise', 'reject')), scores jsonb NOT NULL DEFAULT '{{}}',
        issues jsonb NOT NULL DEFAULT '[]', summary text NOT NULL DEFAULT '', ai_call_id bigint, model varchar(128),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_article_review_version ON article_review (article_version_id, id DESC);
    CREATE TABLE api_token (
        id bigserial PRIMARY KEY, name varchar(64) NOT NULL, token_hash char(64) NOT NULL UNIQUE,
        channel varchar(10) NOT NULL DEFAULT 'extension' CHECK (channel IN ('rpa', 'extension', 'api')),
        account_ids bigint[] NOT NULL DEFAULT '{{}}', active boolean NOT NULL DEFAULT true,
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now(), last_used_at timestamptz
    )""")


def downgrade():
    op.execute(f"""
    DROP TABLE api_token;
    DROP TABLE article_review;
    ALTER TABLE publication_record DROP COLUMN api_token_id, DROP COLUMN reply_url, DROP COLUMN channel;
    ALTER TABLE editorial_account DROP COLUMN reply_explorer_link;
    ALTER TABLE article_version DROP COLUMN reply_text, DROP COLUMN style_key;
    DROP INDEX ix_article_expiry;
    ALTER TABLE article DROP COLUMN claimed_at, DROP COLUMN claimed_by_token, DROP COLUMN expires_at;
    UPDATE article SET status = 'killed' WHERE status = 'expired';
    ALTER TABLE article DROP CONSTRAINT article_status_check;
    ALTER TABLE article ADD CONSTRAINT article_status_check CHECK (status IN ({STATES}))""")
