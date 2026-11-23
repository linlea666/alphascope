from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
NEWSROOM_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSROOM_",
        env_file=(NEWSROOM_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://localhost/newsroom"
    # AlphaScope's read-only feed (backend/app/api/feed.py); the newsroom never calls an exchange itself.
    feed_base_url: str = "http://127.0.0.1:8610"
    feed_token: str = ""
    feed_timeout_seconds: float = 15.0
    sync_page_limit: int = 1000
    sync_max_pages: int = 20
    replay_minutes: int = 60
    # trader context calls hit AlphaScope's API process; keep them well below anything that could slow it down
    context_calls_per_hour: int = 240

    secret_key_path: Path = NEWSROOM_DIR / "data" / "secret.key"
    asset_dir: Path = NEWSROOM_DIR / "data" / "assets"
    session_ttl_hours: int = 12
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    ai_max_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
