"""Record candles for a few reference coins. Incremental: each run fetches from the last stored candle."""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import IngestRun
from app.sources.hl_client import HyperliquidInfoClient

log = logging.getLogger(__name__)

SOURCE = "hyperliquid"
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
API_MAX_CANDLES = 5000


def _csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def candle_rows(coin: str, interval: str, data: list, fetched_at: datetime, source: str = SOURCE) -> list[dict]:
    return [
        {"source": source, "coin": coin, "interval": interval, "t": datetime.fromtimestamp(c["t"] / 1000, tz=UTC),
         "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"], "n": c.get("n"), "f": fetched_at}
        for c in data
    ]


def store_candles(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    # The newest candle is still forming, so existing rows are refreshed rather than skipped.
    session.execute(text("""
        INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
        VALUES (:source, :coin, :interval, :t, :o, :h, :l, :c, :v, :n, :f)
        ON CONFLICT (source, coin, interval, t) DO UPDATE SET o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l,
          c = EXCLUDED.c, v = EXCLUDED.v, n = EXCLUDED.n, fetched_at = EXCLUDED.fetched_at"""), rows)
    return len(rows)


async def record_candles(sf: sessionmaker[Session], settings: Settings) -> dict:
    t0 = time.monotonic()
    with sf() as s:
        run = IngestRun(kind="market", status="running", stats={})
        s.add(run)
        s.commit()
        run_id = run.id
        last = {(c, i): t for c, i, t in s.execute(text(
            "SELECT coin, interval, max(t) FROM market_candle WHERE source = :src GROUP BY coin, interval"),
            {"src": SOURCE}).all()}
    now_ms = int(time.time() * 1000)
    stored: dict[str, int] = {}
    errors: list[str] = []
    budget = settings.hl_weight_per_minute * settings.hl_weight_safety
    async with HyperliquidInfoClient(settings.hl_info_url, budget, settings.hl_timeout_seconds,
                                     settings.hl_max_retries) as client:
        for coin in _csv(settings.market_coins):
            for interval in _csv(settings.market_intervals):
                step = INTERVAL_MS[interval]
                prev = last.get((coin, interval))
                start = int(prev.timestamp() * 1000) if prev else now_ms - step * API_MAX_CANDLES
                try:
                    r = await client.info({"type": "candleSnapshot",
                                           "req": {"coin": coin, "interval": interval, "startTime": start,
                                                   "endTime": now_ms}})
                except Exception as exc:  # noqa: BLE001 — one series must not stop the others
                    errors.append(f"{coin}/{interval}: {exc!r}"[:200])
                    continue
                with sf() as s:
                    stored[f"{coin}/{interval}"] = store_candles(s, candle_rows(coin, interval, r.data,
                                                                                datetime.now(UTC)))
                    s.commit()
        stats = {"stored": stored, "errors": errors, "api": client.usage.as_dict(),
                 "duration_s": round(time.monotonic() - t0, 2)}
    with sf() as s:
        run = s.get(IngestRun, run_id)
        run.status = "success" if not errors else ("partial" if stored else "failed")
        run.finished_at, run.stats = datetime.now(UTC), stats
        s.commit()
    return {"run_id": run_id, "status": run.status, **stats}


def daily_closes(session: Session, coin: str, since: datetime | None = None,
                 source: str = SOURCE) -> list[tuple[datetime, float, float, float]]:
    """(day start, high, low, close) for daily candles, oldest first."""
    rows = session.execute(text("""
        SELECT t, h, l, c FROM market_candle WHERE source = :source AND coin = :coin AND interval = '1d'
          AND (CAST(:since AS timestamptz) IS NULL OR t >= :since) ORDER BY t"""),
        {"source": source, "coin": coin, "since": since}).all()
    # Positional access: Row.t is SQLAlchemy's tuple accessor, not the column named t.
    return [(t, float(h), float(lo), float(c)) for t, h, lo, c in rows]


if __name__ == "__main__":  # pragma: no cover - manual run
    from app.config import get_settings
    from app.db import session_factory

    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(record_candles(session_factory(), get_settings())))
