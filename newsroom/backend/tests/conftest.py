import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).parent.parent
TEST_DB_URL = os.environ.get("NEWSROOM_TEST_DATABASE_URL", "postgresql+psycopg://localhost/newsroom_test")
os.environ.setdefault("NEWSROOM_DATABASE_URL", TEST_DB_URL)
TABLES = ("ai_call_log", "job", "ai_agent_profile", "prompt_template", "ai_model_config", "performance_snapshot",
          "correction_record", "publication_record", "publication_plan", "media_asset", "article_fact_reference",
          "experiment_vote", "experiment_pair", "draft_rating", "article_version", "article", "assignment", "eic_evaluation", "account_permission", "editorial_account",
          "editorial_style", "story_event", "fact_packet", "event_evidence", "event_state_history", "news_event",
          "news_story", "source_event", "position_episode", "trader_context_snapshot", "trader",
          "source_health_snapshot", "sync_run", "ingestion_checkpoint_history", "ingestion_checkpoint",
          "source_connection", "system_setting", "audit_log", "user_session", "user_role", "app_user")


def alembic(*args):
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"db_url={TEST_DB_URL}", *args], cwd=BACKEND, check=True,
                   capture_output=True)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL, connect_args={"options": "-c timezone=UTC"})
    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unavailable: {exc}")
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    return engine


@pytest.fixture
def sf(db_engine):
    with db_engine.begin() as conn:
        # TRUNCATE does not fire the append-only row triggers
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _secret_key(tmp_path, monkeypatch):
    from nr.config import get_settings
    monkeypatch.setattr(get_settings(), "secret_key_path", tmp_path / "secret.key")
    monkeypatch.setattr(get_settings(), "asset_dir", tmp_path / "assets")


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    import nr.ai.runner
    monkeypatch.setattr(nr.ai.runner.time, "sleep", lambda _: None)
