"""Prompt versions stay immutable, but one that nothing ever used (no agent version, no AI call, no draft) can be
deleted. Anything a draft or a call points at stays, so every published post can still be traced to its prompt."""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION nr_prompt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'prompt_template is append-only';
        END IF;
        IF EXISTS (SELECT 1 FROM ai_agent_profile WHERE prompt_key = OLD.key AND prompt_version = OLD.version)
           OR EXISTS (SELECT 1 FROM ai_call_log WHERE prompt_ref = OLD.key || '@v' || OLD.version)
           OR EXISTS (SELECT 1 FROM article_version WHERE prompt_ref = OLD.key || '@v' || OLD.version) THEN
            RAISE EXCEPTION 'prompt % v% has been used and cannot be deleted', OLD.key, OLD.version;
        END IF;
        RETURN OLD;
    END $$;
    DROP TRIGGER prompt_template_append_only ON prompt_template;
    CREATE TRIGGER prompt_template_guard BEFORE UPDATE OR DELETE ON prompt_template
        FOR EACH ROW EXECUTE FUNCTION nr_prompt_guard();
    CREATE INDEX IF NOT EXISTS ix_ai_call_prompt ON ai_call_log (prompt_ref)""")


def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS ix_ai_call_prompt;
    DROP TRIGGER prompt_template_guard ON prompt_template;
    CREATE TRIGGER prompt_template_append_only BEFORE UPDATE OR DELETE ON prompt_template
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    DROP FUNCTION nr_prompt_guard()""")
