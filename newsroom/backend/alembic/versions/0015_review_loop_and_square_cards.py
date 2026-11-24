"""Learning from the reviewer (docs/17): the reviewer's revision note is kept so a person can ask for that revision
later; posts the reviewer or readers liked become candidate style examples a person adopts; the prompt coach drafts
new prompt versions from the week's review issues and tests them in an experiment. Square cards draw traders'
public avatars, cached locally."""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE article_review ADD COLUMN revision_note text NOT NULL DEFAULT '';
    ALTER TABLE trader ADD COLUMN avatar_url text, ADD COLUMN avatar_path text, ADD COLUMN avatar_fetched_at timestamptz;
    ALTER TABLE editorial_account ADD COLUMN show_avatar boolean NOT NULL DEFAULT true;

    CREATE TABLE style_example_candidate (
        id bigserial PRIMARY KEY, style_key varchar(64) NOT NULL, article_version_id bigint NOT NULL REFERENCES article_version,
        publication_id bigint REFERENCES publication_record, original text NOT NULL, example text NOT NULL,
        reason varchar(16) NOT NULL CHECK (reason IN ('review', 'engagement')), score jsonb NOT NULL DEFAULT '{}',
        status varchar(10) NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'adopted', 'dismissed')),
        decided_by bigint REFERENCES app_user, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (article_version_id)
    );

    CREATE TABLE prompt_proposal (
        id bigserial PRIMARY KEY, prompt_key varchar(64) NOT NULL, language varchar(2), base_version integer NOT NULL,
        version integer NOT NULL, rationale text NOT NULL, changes jsonb NOT NULL DEFAULT '[]',
        issues jsonb NOT NULL DEFAULT '[]', ai_call_id bigint, experiment_name varchar(64),
        status varchar(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'testing', 'adopted', 'rejected')),
        decided_by bigint REFERENCES app_user, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (prompt_key, version, language)
    )""")


def downgrade():
    op.execute("""
    DROP TABLE prompt_proposal; DROP TABLE style_example_candidate;
    ALTER TABLE editorial_account DROP COLUMN show_avatar;
    ALTER TABLE trader DROP COLUMN avatar_url, DROP COLUMN avatar_path, DROP COLUMN avatar_fetched_at;
    ALTER TABLE article_review DROP COLUMN revision_note""")
