"""Binance USDT-M positioning history for the research coins from the public bulk archive: open interest,
top-trader account and position long/short ratios, the all-account ratio and the taker buy/sell volume ratio, every
5 minutes (daily `metrics` files, checksum-verified and recorded in market_file). The same run extends the monthly
funding settlements back to the same start, so historical factors can use both."""

import asyncio
import csv
import io
import json
import math
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import text

from app.ingest.binance_ingest import month_starts
from app.research import funding_history
from app.sources.binance_bulk import SOURCE, fetch_verified

COLUMNS = {"sum_open_interest": "oi", "sum_open_interest_value": "oi_usd",
           "count_toptrader_long_short_ratio": "top_account_ratio",
           "sum_toptrader_long_short_ratio": "top_position_ratio", "count_long_short_ratio": "account_ratio",
           "sum_taker_long_short_vol_ratio": "taker_ratio"}
CONCURRENCY = 4


def metrics_path(coin: str, day: date) -> str:
    return f"daily/metrics/{coin}USDT/{coin}USDT-metrics-{day:%Y-%m-%d}.zip"


def parse_metrics(body: bytes, coin: str, day: date) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one csv in archive, found {names}")
        rows = {}
        for raw in csv.DictReader(io.StringIO(z.read(names[0]).decode())):
            if raw["symbol"] != coin + "USDT":
                raise ValueError("unexpected symbol")
            t = datetime.strptime(raw["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            if t.date() != day:
                raise ValueError("row outside the file's day")
            row = {"coin": coin, "t": t}
            for src, dst in COLUMNS.items():
                v = float(raw[src]) if raw.get(src) not in (None, "") else None
                row[dst] = v if v is not None and math.isfinite(v) and v >= 0 else None
            rows[t] = row
    return [rows[t] for t in sorted(rows)]


async def sync_metrics(sf, coins, start: date, max_files: int = 300) -> dict:
    """Fetch missing daily files (oldest first, at most max_files) up to yesterday, plus missing funding months."""
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    with sf() as s:
        done = set(s.scalars(text("SELECT path FROM market_file WHERE source = :s AND interval = 'metrics'"),
                             {"s": SOURCE}))
        funded = set(s.execute(text("SELECT coin, month FROM funding_archive_month WHERE venue = 'binance'")).all())
    days = [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]
    plan = [(c, d) for d in days for c in coins if metrics_path(c, d) not in done][:max_files]
    stats = {"files": 0, "rows": 0, "unpublished": 0, "errors": 0, "funding_months": 0}
    gate = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=30) as client:

        async def one(coin, day):
            path = metrics_path(coin, day)
            try:
                async with gate:
                    got = await fetch_verified(client, path)
                if got is None:
                    stats["unpublished"] += 1
                    return
                rows = parse_metrics(got[0], coin, day)
            except (httpx.HTTPError, ValueError, KeyError, zipfile.BadZipFile):
                stats["errors"] += 1
                return
            with sf.begin() as s:
                if rows:
                    s.execute(text("""INSERT INTO binance_metric (coin, t, oi, oi_usd, top_account_ratio,
                        top_position_ratio, account_ratio, taker_ratio) VALUES (:coin, :t, :oi, :oi_usd,
                        :top_account_ratio, :top_position_ratio, :account_ratio, :taker_ratio)
                        ON CONFLICT (coin, t) DO NOTHING"""), rows)
                s.execute(text("""INSERT INTO market_file (source, path, coin, interval, period, sha256, rows, imported_at)
                    VALUES (:s, :p, :c, 'metrics', :per, :sha, :n, now()) ON CONFLICT DO NOTHING"""),
                    {"s": SOURCE, "p": path, "c": coin, "per": str(day), "sha": got[1], "n": len(rows)})
            stats["files"] += 1
            stats["rows"] += len(rows)

        async def funding(coin, month):
            try:
                async with gate:
                    source, rows = await funding_history.fetch_month(client, "binance", coin, month)
            except (httpx.HTTPError, ValueError, KeyError, zipfile.BadZipFile, IndexError):
                return
            with sf.begin() as s:
                s.execute(text("""INSERT INTO funding_archive_month(venue,coin,month,source,settlements)
                    VALUES('binance',:c,:m,:url,CAST(:rows AS jsonb)) ON CONFLICT DO NOTHING"""),
                    {"c": coin, "m": month, "url": source, "rows": json.dumps(rows)})
            stats["funding_months"] += 1

        last_month = yesterday.replace(day=1) - timedelta(days=1)
        months = [(c, m) for m in month_starts(start, last_month) for c in coins if (c, m) not in funded]
        await asyncio.gather(*(one(c, d) for c, d in plan), *(funding(c, m) for c, m in months))
    stats["status"] = "partial" if stats["errors"] or len(plan) == max_files else "success"
    return stats
