"""Trial drafts (try a model on any event, any account, without touching the event's workflow) and a trash for
articles. Versions stay append-only: trashing hides an article and can be undone."""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE assignment DROP CONSTRAINT assignment_source_check;
    ALTER TABLE assignment ADD CONSTRAINT assignment_source_check CHECK (source IN ('eic_ai', 'editor', 'trial'));
    ALTER TABLE article ADD COLUMN deleted_at timestamptz, ADD COLUMN deleted_by bigint REFERENCES app_user,
        ADD COLUMN deleted_reason text;
    CREATE INDEX ix_article_deleted ON article (deleted_at) WHERE deleted_at IS NOT NULL;
    -- a trial must never take the slot of the real assignment for the same event and account
    DROP INDEX ux_assignment_active;
    CREATE UNIQUE INDEX ux_assignment_active ON assignment (news_event_id, account_id)
        WHERE status NOT IN ('rejected', 'cancelled') AND source <> 'trial'""")


def downgrade():
    op.execute("""
    DROP INDEX ux_assignment_active;
    UPDATE assignment SET status = 'cancelled' WHERE source = 'trial';
    CREATE UNIQUE INDEX ux_assignment_active ON assignment (news_event_id, account_id)
        WHERE status NOT IN ('rejected', 'cancelled');
    DROP INDEX ix_article_deleted;
    ALTER TABLE article DROP COLUMN deleted_reason, DROP COLUMN deleted_by, DROP COLUMN deleted_at;
    UPDATE assignment SET source = 'editor' WHERE source = 'trial';
    ALTER TABLE assignment DROP CONSTRAINT assignment_source_check;
    ALTER TABLE assignment ADD CONSTRAINT assignment_source_check CHECK (source IN ('eic_ai', 'editor'))""")
