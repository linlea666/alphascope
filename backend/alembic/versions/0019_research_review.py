"""Keep manual factor reviews with their prospective evidence."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE research_review(
          id bigserial PRIMARY KEY,
          evaluation_id bigint NOT NULL REFERENCES research_evaluation(id),
          experiment_id bigint NOT NULL REFERENCES research_experiment(id),
          decision varchar(16) NOT NULL,
          rationale text NOT NULL,
          reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp())
    """)
    op.create_index("ix_research_review_experiment", "research_review", ["experiment_id", "reviewed_at"])


def downgrade():
    op.drop_table("research_review")
