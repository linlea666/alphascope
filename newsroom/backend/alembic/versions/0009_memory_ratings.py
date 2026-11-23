"""Story memory (open questions carried from one post to the next), editor ratings of draft versions, and blind A/B
experiments: pairs of drafts for the same event that editors judge without knowing which arm wrote which."""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE news_story ADD COLUMN open_questions jsonb NOT NULL DEFAULT '[]';
    ALTER TABLE article_version ADD COLUMN why_now text NOT NULL DEFAULT '', ADD COLUMN open_question text,
        ADD COLUMN lede_fact_ids text[] NOT NULL DEFAULT '{}';
    CREATE TABLE draft_rating (
        id bigserial PRIMARY KEY, article_version_id bigint NOT NULL REFERENCES article_version,
        user_id bigint NOT NULL REFERENCES app_user, score smallint NOT NULL CHECK (score BETWEEN 1 AND 5),
        tags text[] NOT NULL DEFAULT '{}', note text NOT NULL DEFAULT '', blind boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (article_version_id, user_id)
    );
    CREATE TABLE experiment_pair (
        id bigserial PRIMARY KEY, experiment varchar(48) NOT NULL, news_event_id bigint NOT NULL REFERENCES news_event,
        stratum varchar(32) NOT NULL, article_id bigint NOT NULL REFERENCES article,
        a_version_id bigint REFERENCES article_version, b_version_id bigint REFERENCES article_version,
        a_error text, b_error text, a_first boolean NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (experiment, news_event_id)
    );
    CREATE TABLE experiment_vote (
        pair_id bigint NOT NULL REFERENCES experiment_pair, user_id bigint NOT NULL REFERENCES app_user,
        winner varchar(4) NOT NULL CHECK (winner IN ('a', 'b', 'tie')),
        misleading_a boolean NOT NULL DEFAULT false, misleading_b boolean NOT NULL DEFAULT false,
        scores jsonb NOT NULL DEFAULT '{}', note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (pair_id, user_id)
    )""")


def downgrade():
    op.execute("""
    DROP TABLE experiment_vote;
    DROP TABLE experiment_pair;
    DROP TABLE draft_rating;
    ALTER TABLE article_version DROP COLUMN lede_fact_ids, DROP COLUMN open_question, DROP COLUMN why_now;
    ALTER TABLE news_story DROP COLUMN open_questions""")
