import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND = Path(__file__).parent.parent
TEST_DB_URL = os.environ.get("ALPHASCOPE_TEST_DATABASE_URL", "postgresql+psycopg://localhost/alphascope_test")
TABLES = ("coin_limit", "copy_backtest", "copy_score", "trader_health", "sentinel_hit", "zone_sentinel", "daily_digest", "style_tag", "factor_value", "market_snapshot", "market_event", "trader_position", "position_watch",
          "okx_presence", "okx_leaderboard_row", "okx_leaderboard_run", "okx_trader", "okx_position", "okx_instrument",
          "sim_equity", "sim_trade", "sim_position", "copy_sim", "notification", "position_event", "live_position", "live_account", "alert_subscription", "app_setting",
          "watch_item", "watch_group", "wallet_alias", "leg_capture", "timing_score", "decision_timing", "timing_pivot", "trade_decision", "position_cycle", "market_symbol", "market_file", "fill", "fill_sync", "wallet_swing_profile", "wallet_leg_return", "swing_leg", "market_candle",
          "position_snapshot", "account_snapshot", "wallet_metric", "equity_point", "wallet_tier_history",
          "leaderboard_row", "leaderboard_snapshot", "raw_object", "wallet", "ingest_run")


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL, connect_args={"options": "-c timezone=UTC"})
    try:
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unavailable: {exc}")
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", "-x", f"db_url={TEST_DB_URL}", *cmd], cwd=BACKEND,
                       check=True, capture_output=True)
    return engine


@pytest.fixture
def sf(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=db_engine, expire_on_commit=False)
