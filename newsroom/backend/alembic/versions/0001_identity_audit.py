"""Users, roles, sessions, the append-only audit log and system settings."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = depends_on = None

ROLES = "'admin', 'editor_in_chief', 'editor', 'reporter', 'reviewer', 'account_operator', 'analyst'"


def upgrade():
    # shared by every append-only table: history is never rewritten, only superseded
    op.execute("""CREATE FUNCTION nr_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END $$""")
    op.execute(f"""CREATE TABLE app_user (
        id bigserial PRIMARY KEY, username varchar(64) NOT NULL, display_name varchar(128),
        password_hash text NOT NULL, is_active boolean NOT NULL DEFAULT true,
        must_change_password boolean NOT NULL DEFAULT false, failed_logins integer NOT NULL DEFAULT 0,
        locked_until timestamptz, created_at timestamptz NOT NULL DEFAULT now(), last_login_at timestamptz
    );
    CREATE UNIQUE INDEX ux_app_user_username ON app_user (lower(username));
    CREATE TABLE user_role (
        user_id bigint NOT NULL REFERENCES app_user ON DELETE CASCADE,
        role varchar(24) NOT NULL CHECK (role IN ({ROLES})), PRIMARY KEY (user_id, role)
    );
    CREATE TABLE user_session (
        id bigserial PRIMARY KEY, token_hash char(64) NOT NULL UNIQUE,
        user_id bigint NOT NULL REFERENCES app_user ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
        last_seen_at timestamptz, ip varchar(64), user_agent varchar(256), revoked_at timestamptz
    );
    CREATE INDEX ix_user_session_user ON user_session (user_id);
    CREATE TABLE audit_log (
        id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT clock_timestamp(),
        actor_kind varchar(8) NOT NULL CHECK (actor_kind IN ('user', 'system', 'ai')),
        actor_user_id bigint REFERENCES app_user, action varchar(64) NOT NULL, object_type varchar(32) NOT NULL,
        object_id varchar(64), account_id bigint, before jsonb, after jsonb, reason text
    );
    CREATE INDEX ix_audit_object ON audit_log (object_type, object_id);
    CREATE INDEX ix_audit_at ON audit_log (at);
    CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION nr_append_only();
    CREATE TABLE system_setting (
        key varchar(64) PRIMARY KEY, value jsonb NOT NULL DEFAULT '{{}}', secret text,
        updated_at timestamptz NOT NULL DEFAULT now(), updated_by bigint REFERENCES app_user
    )""")


def downgrade():
    op.execute("""DROP TABLE system_setting; DROP TABLE audit_log; DROP TABLE user_session; DROP TABLE user_role;
                  DROP TABLE app_user; DROP FUNCTION nr_append_only()""")
