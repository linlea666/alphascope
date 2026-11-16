from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALPHASCOPE_",
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://localhost/alphascope"
    raw_store_dir: Path = BACKEND_DIR.parent / "data" / "raw"

    hl_info_url: str = "https://api.hyperliquid.xyz/info"
    hl_leaderboard_url: str = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
    # Hyperliquid allows 1200 weight/min per IP; we only use a share of it.
    hl_weight_per_minute: int = 1200
    hl_weight_safety: float = 0.5
    hl_timeout_seconds: float = 60.0
    hl_max_retries: int = 4
    enrich_concurrency: int = 4

    leaderboard_interval_minutes: int = 60
    # Full normalized leaderboard rows are persisted at most this often;
    # every fetch is still archived raw.
    leaderboard_snapshot_hours: int = 6

    enrich_batch_size: int = 200
    enrich_interval_minutes: int = 10
    portfolio_refresh_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
