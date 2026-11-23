"""Accounts can have the system add the full address and an explorer link as the last post of a thread; experiments
record what each arm used (prompt version, style, model), so any two set-ups can be compared; a version names the
card drawn for it."""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE editorial_account ADD COLUMN link_in_thread boolean NOT NULL DEFAULT true;
    ALTER TABLE article_version ADD COLUMN card_template varchar(32);
    CREATE TABLE experiment (
        name varchar(48) PRIMARY KEY, arms jsonb NOT NULL, account_id bigint REFERENCES editorial_account,
        created_by bigint REFERENCES app_user, created_at timestamptz NOT NULL DEFAULT now()
    )""")


def downgrade():
    op.execute("""DROP TABLE experiment; ALTER TABLE article_version DROP COLUMN card_template;
                  ALTER TABLE editorial_account DROP COLUMN link_in_thread""")
