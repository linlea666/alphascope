"""A prompt version can hold one text per language (written natively, not translated): a Chinese account's draft
uses the zh text, an English account's the en text, and a version without one falls back to the shared text.
Calls and drafts record which one ran (reporter@v4.zh), and the delete guard knows that form."""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None

GUARD = """
    CREATE OR REPLACE FUNCTION nr_prompt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE ref text := OLD.key || '@v' || OLD.version{suffix};
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'prompt_template is append-only';
        END IF;
        IF EXISTS (SELECT 1 FROM ai_agent_profile WHERE prompt_key = OLD.key AND prompt_version = OLD.version)
           OR EXISTS (SELECT 1 FROM ai_call_log WHERE prompt_ref = ref)
           OR EXISTS (SELECT 1 FROM article_version WHERE prompt_ref = ref) THEN
            RAISE EXCEPTION 'prompt % v% has been used and cannot be deleted', OLD.key, OLD.version;
        END IF;
        RETURN OLD;
    END $$"""


def upgrade():
    op.execute("""
    ALTER TABLE prompt_template ADD COLUMN language varchar(2) CHECK (language IN ('zh', 'en'));
    ALTER TABLE prompt_template DROP CONSTRAINT prompt_template_key_version_key;
    CREATE UNIQUE INDEX ux_prompt_template ON prompt_template (key, version, coalesce(language, ''))""")
    op.execute(GUARD.replace("{suffix}", " || coalesce('.' || OLD.language, '')"))


def downgrade():
    op.execute(GUARD.replace("{suffix}", ""))
    op.execute("""
    ALTER TABLE prompt_template DISABLE TRIGGER prompt_template_guard;
    DELETE FROM prompt_template WHERE language IS NOT NULL;
    ALTER TABLE prompt_template ENABLE TRIGGER prompt_template_guard;
    DROP INDEX ux_prompt_template;
    ALTER TABLE prompt_template ADD CONSTRAINT prompt_template_key_version_key UNIQUE (key, version);
    ALTER TABLE prompt_template DROP COLUMN language""")
