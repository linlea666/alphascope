"""OKX candles for coins that only OKX traders trade (no Binance or Hyperliquid series), so their positions can
be scored and replayed. One series per coin: the USDT-margined perpetual. Backfill reaches the coin's first OKX
decision (not before 2025, when timing pivots start), increments follow the newest bar.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.okx.client import OkxClient, OkxError

log = logging.getLogger(__name__)

BARS = {"1h": ("1H", 3600), "4h": ("4H", 14400), "1d": ("1Dutc", 86400)}
EARLIEST = datetime(2025, 1, 1, tzinfo=UTC)


def coins_needing(s: Session, limit: int = 400) -> list[tuple[str, datetime]]:
    """OKX-traded coins without a live Binance / Hyperliquid hourly series, busiest first, with their first use."""
    return [tuple(r) for r in s.execute(text("""
        SELECT p.coin, min(p.open_time) AS first_at FROM okx_position p
        JOIN okx_instrument i ON i.inst_id = p.coin || '-USDT-SWAP'
        WHERE p.close_time > now() - interval '400 days'
          AND NOT EXISTS (SELECT 1 FROM market_candle m WHERE m.coin = p.coin AND m.interval = '1h'
                            AND m.source IN ('binance_um', 'hyperliquid') AND m.t > now() - interval '3 days')
        GROUP BY p.coin ORDER BY count(*) DESC LIMIT :n"""), {"n": limit}).all()]


def _store(s: Session, coin: str, interval: str, rows: list[list], now: datetime) -> int:
    vals = [{"c": coin, "iv": interval, "t": datetime.fromtimestamp(int(r[0]) / 1000, UTC), "o": r[1], "h": r[2],
             "l": r[3], "cl": r[4], "v": r[5] or 0, "now": now} for r in rows if len(r) > 8 and str(r[8]) == "1"]
    if vals:
        s.execute(text("""
            INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, fetched_at)
            VALUES ('okx', :c, :iv, :t, :o, :h, :l, :cl, :v, :now)
            ON CONFLICT (source, coin, interval, t) DO UPDATE SET o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l,
              c = EXCLUDED.c, v = EXCLUDED.v, fetched_at = EXCLUDED.fetched_at"""), vals)
    return len(vals)


async def sync_candles(sf: sessionmaker[Session], client: OkxClient, max_requests: int = 400,
                       now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    start_requests = client.usage.requests
    out = {"coins": 0, "bars": 0, "complete": True}
    with sf() as s:
        coins = coins_needing(s)
    for coin, first_at in coins:
        inst = f"{coin}-USDT-SWAP"
        target = max(EARLIEST, first_at - timedelta(days=30))  # room for the timing window before the first trade
        for interval, (bar, secs) in BARS.items():
            with sf() as s:
                lo, hi = s.execute(text("""SELECT min(t), max(t) FROM market_candle
                                           WHERE source = 'okx' AND coin = :c AND interval = :iv"""),
                                   {"c": coin, "iv": interval}).one()
            # increments down to the newest stored bar, then backfill below the oldest one
            plans = [(None, hi)] if hi else [(None, target)]
            if lo and lo > target + timedelta(seconds=secs):
                plans.append((int(lo.timestamp() * 1000), target))
            for after, stop in plans:
                while True:
                    if client.usage.requests - start_requests >= max_requests:
                        out["complete"] = False
                        return out
                    try:
                        rows = await client.candles(inst, bar, after)
                    except OkxError as exc:
                        log.info("okx candles %s %s: %s", inst, bar, exc)
                        break
                    if not rows:
                        break
                    with sf() as s:
                        out["bars"] += _store(s, coin, interval, rows, now)
                        s.commit()
                    oldest = min(int(r[0]) for r in rows)
                    if oldest <= int(stop.timestamp() * 1000) or len(rows) < 100:
                        break
                    after = oldest
        out["coins"] += 1
    return out
