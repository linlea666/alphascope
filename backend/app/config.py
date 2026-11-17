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

    # Candles are recorded from now on (the API keeps only the latest 5000 per interval).
    market_coins: str = "BTC,ETH,SOL,HYPE"
    market_intervals: str = "1d,4h,1h,15m,1m"
    market_interval_minutes: int = 60
    # Also record Hyperliquid candles for the most-traded archived coins Binance cannot cover
    # (HIP-3 stocks/commodities, Hyperliquid-only tokens); no 1m for these to keep volume bounded.
    market_fill_coins: int = 40
    market_fill_intervals: str = "1d,4h,1h,15m"

    # Binance USDT-M klines from data.binance.vision: core coins with long history, plus the most-traded
    # coins of archived wallets from shortly before the fill archive starts.
    binance_core_coins: str = "BTC,ETH,SOL"
    binance_core_start: str = "2025-01-01"
    binance_fill_coins: int = 40
    binance_fill_start: str = "2026-06-01"
    binance_interval_hours: int = 6

    # Swing research: fills of candidates are archived from this date (start of the current cycle).
    swing_coin: str = "BTC"
    swing_fill_start: str = "2026-07-01"
    swing_max_candidates: int = 600
    # Broader fill archive for price-zone research (who bought the lows / sold the highs): clean active
    # tier-B wallets, lowest turnover first (their older fills are still inside the API window).
    macro_scan_max: int = 1500
    fill_batch_size: int = 15
    fill_interval_minutes: int = 10
    fill_max_pages: int = 12

    # Live monitor (alphascope-live): its own share of the per-IP budget, on top of hl_weight_safety.
    live_weight_safety: float = 0.2
    # Fernet key for secrets stored in app_setting (SMTP password); created with mode 600 on first use.
    secret_key_path: Path = BACKEND_DIR.parent / "data" / "secret.key"

    # OKX copy-trading source (docs/11): public priapi endpoints, a request budget separate from Hyperliquid.
    okx_base_url: str = "https://www.okx.com"
    okx_requests_per_minute: int = 120  # worker share; measured: ~180/min from the server without throttling
    okx_live_requests_per_minute: int = 60  # live monitor share (position-current polling)
    okx_timeout_seconds: float = 20.0
    okx_max_retries: int = 4
    okx_page_size: int = 50  # leaderboard page size (OKX accepts up to 50)
    okx_daily_hour_local: float = 8.5  # daily 7d crawl after 08:30 in live.timezone (Asia/Shanghai by default)
    okx_extra_window_pages: int = 2  # 30d / 90d / 365d boards: first pages only (top 100 each)
    okx_history_batch: int = 60  # traders per history run
    okx_history_max_pages: int = 30  # pages of 100 positions per trader per run
    okx_history_target_days: int = 365

    enrich_batch_size: int = 200
    enrich_interval_minutes: int = 10
    portfolio_refresh_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
