"""Import Binance USDT-M 1m klines for reference coins and the coins our archived wallets trade.

1. Symbol map: Hyperliquid coin -> Binance symbol (kPEPE -> 1000PEPEUSDT), validated by comparing
   Hyperliquid prices (archived fills, else recorded HL candles) with Binance closes at the same minute.
2. Files: complete months from monthly archives (falling back to daily files until the monthly one is
   published), the current month from daily files up to yesterday. Every file is checksum-verified and
   recorded in market_file, so reruns only fetch what is missing.
3. 15m / 1h / 4h / 1d are aggregated locally from 1m so all intervals agree with each other.
"""

import asyncio
import calendar
import logging
import statistics
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import IngestRun
from app.sources.binance_bulk import SOURCE, ChecksumError, Kline, daily_path, fetch_verified, monthly_path, parse_klines

log = logging.getLogger(__name__)

AGG_INTERVALS = {"15m": "15 minutes", "1h": "1 hour", "4h": "4 hours", "1d": "1 day"}
MAX_RATIO_DEVIATION = 0.01  # Hyperliquid/Binance median price ratio must be within 1% of the multiplier
MIN_SAMPLES = 10  # enough to tell a different instrument/unit (ratios cluster tightly near 1)
CONCURRENCY = 4


def _csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def candidate_symbol(coin: str) -> tuple[str | None, Decimal, str | None]:
    """(symbol, multiplier, reason-if-unsupported). Multiplier: HL price = Binance price * multiplier."""
    if ":" in coin or coin.startswith("@"):
        return None, Decimal(1), "HIP-3 / spot market: no Binance equivalent"
    if len(coin) > 1 and coin[0] == "k" and coin[1:].isupper():
        return f"1000{coin[1:]}USDT", Decimal(1), None  # both quote the price of 1000 units
    return f"{coin}USDT", Decimal(1), None


def month_starts(start: date, end: date) -> list[date]:
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def plan_files(symbol: str, start: date, today: date, done: set[str]) -> list[tuple[str, str, str]]:
    """[(path, period, kind)] still missing, oldest first. Complete months prefer the monthly file."""
    plan: list[tuple[str, str, str]] = []
    yesterday = today - timedelta(days=1)
    for m in month_starts(start, yesterday):
        last = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
        complete = last < today
        mpath = monthly_path(symbol, "1m", m)
        if complete and mpath in done:
            continue
        if complete:
            plan.append((mpath, f"{m:%Y-%m}", "monthly"))
        first_day = max(m, start)
        for i in range((min(last, yesterday) - first_day).days + 1):
            d = first_day + timedelta(days=i)
            dpath = daily_path(symbol, "1m", d)
            if dpath not in done:
                plan.append((dpath, f"{d:%Y-%m-%d}", "daily"))
    return plan


def coins_to_import(session: Session, settings: Settings) -> dict[str, date]:
    """Core coins from core_start; plus the most-traded coins of archived wallets from fill_start."""
    coins = {c: date.fromisoformat(settings.binance_core_start) for c in _csv(settings.binance_core_coins)}
    top = session.execute(text("""
        SELECT coin FROM fill WHERE coin NOT LIKE '%:%' AND coin NOT LIKE '@%'
        GROUP BY coin ORDER BY sum(px * sz) DESC LIMIT :n"""), {"n": settings.binance_fill_coins}).scalars().all()
    for c in top:
        coins.setdefault(c, date.fromisoformat(settings.binance_fill_start))
    return coins


def reference_prices(session: Session, coin: str, day: date) -> list[tuple[datetime, float]]:
    """Hyperliquid prices on a given day: archived fills, topped up with recorded Hyperliquid candle closes
    (a candle's close is the price at its last minute)."""
    a, b = day, day + timedelta(days=1)
    refs = [(t, float(p)) for t, p in session.execute(text("""
        SELECT time, px FROM fill WHERE coin = :c AND time >= :a AND time < :b ORDER BY time LIMIT 2000"""),
        {"c": coin, "a": a, "b": b}).all()]
    if len(refs) < MIN_SAMPLES:
        refs += [(t + (timedelta(hours=1) if iv == "1h" else timedelta(0)) - timedelta(minutes=1), float(c))
                 for t, iv, c in session.execute(text("""
            SELECT t, interval, c FROM market_candle WHERE source = 'hyperliquid' AND coin = :c
              AND interval = '1h' AND t >= :a AND t < :b ORDER BY t"""), {"c": coin, "a": a, "b": b}).all()]
    return refs


def best_validation_day(session: Session, coin: str, today: date) -> date | None:
    row = session.execute(text("""
        SELECT time::date AS d FROM fill WHERE coin = :c AND time >= :since AND time < :today
        GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"""),
        {"c": coin, "since": today - timedelta(days=30), "today": today}).first()
    if row:
        return row.d
    row = session.execute(text("""
        SELECT t::date AS d FROM market_candle WHERE source = 'hyperliquid' AND coin = :c AND interval = '1h'
          AND t < :today ORDER BY t DESC LIMIT 1"""), {"c": coin, "today": today}).first()
    return row.d if row else None


def compare(ref: list[tuple[datetime, float]], klines: list[Kline], multiplier: Decimal) -> tuple[float, float, int]:
    """(median HL/Binance ratio divided by multiplier, p95 |basis| in bps, samples)."""
    closes = {k.t.replace(second=0, microsecond=0): float(k.c) for k in klines}
    ratios = []
    for t, px in ref:
        c = closes.get(t.replace(second=0, microsecond=0))
        if c:
            ratios.append(px / (c * float(multiplier)))
    if not ratios:
        return float("nan"), float("nan"), 0
    med = statistics.median(ratios)
    bps = sorted(abs(r / med - 1) * 1e4 for r in ratios)
    return med, bps[int(0.95 * (len(bps) - 1))], len(ratios)


async def validate_symbol(client: httpx.AsyncClient, session: Session, coin: str, today: date) -> dict:
    symbol, mult, reason = candidate_symbol(coin)
    rec = {"coin": coin, "source": SOURCE, "symbol": symbol, "multiplier": mult, "status": "unsupported",
           "median_ratio": None, "basis_p95_bps": None, "n_samples": 0, "notes": reason}
    if symbol is None:
        return rec
    day = best_validation_day(session, coin, today)
    if day is None:
        rec.update(status="unavailable", notes="no Hyperliquid reference prices to validate against")
        return rec
    got = await fetch_verified(client, daily_path(symbol, "1m", day))
    if got is None:
        rec.update(status="unavailable", notes=f"{symbol} not in the Binance archive for {day}")
        return rec
    med, p95, n = compare(reference_prices(session, coin, day), parse_klines(got[0]), mult)
    rec.update(median_ratio=med if n else None, basis_p95_bps=p95 if n else None, n_samples=n)
    if n < MIN_SAMPLES:
        rec.update(status="invalid", notes=f"only {n} overlapping samples on {day}")
    elif abs(med - 1) > MAX_RATIO_DEVIATION:
        rec.update(status="invalid", notes=f"median price ratio {med:.4f} on {day}: different instrument or unit")
    else:
        rec.update(status="valid", notes=f"validated on {day}")
    return rec


def store_klines(session: Session, coin: str, klines: list[Kline], now: datetime) -> int:
    if not klines:
        return 0
    session.execute(text("CREATE TEMP TABLE IF NOT EXISTS tmp_kl (LIKE market_candle INCLUDING DEFAULTS) ON COMMIT DROP"))
    pg = session.connection().connection.driver_connection
    with pg.cursor() as cur, cur.copy(
            "COPY tmp_kl (source, coin, interval, t, o, h, l, c, v, n, fetched_at) FROM STDIN") as copy:
        for k in klines:
            copy.write_row((SOURCE, coin, "1m", k.t, k.o, k.h, k.l, k.c, k.v, k.n, now))
    session.execute(text("""
        INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at) SELECT source, coin, interval, t, o, h, l, c, v, n, fetched_at FROM tmp_kl
        ON CONFLICT (source, coin, interval, t) DO UPDATE SET o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l,
          c = EXCLUDED.c, v = EXCLUDED.v, n = EXCLUDED.n, fetched_at = EXCLUDED.fetched_at"""))
    session.execute(text("TRUNCATE tmp_kl"))
    return len(klines)


def aggregate(session: Session, coin: str, start: datetime, end: datetime) -> None:
    """Rebuild higher intervals from 1m for whole buckets inside [start, end)."""
    for interval, step in AGG_INTERVALS.items():
        session.execute(text("""
            INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
            SELECT :src, :coin, :iv, b, (array_agg(o ORDER BY t))[1], max(h), min(l),
                   (array_agg(c ORDER BY t DESC))[1], sum(v), sum(n), now()
            FROM (SELECT *, date_bin(CAST(:step AS interval), t, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS b
                  FROM market_candle WHERE source = :src AND coin = :coin AND interval = '1m'
                    AND t >= :a AND t < :z) x
            GROUP BY b
            ON CONFLICT (source, coin, interval, t) DO UPDATE SET o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l,
              c = EXCLUDED.c, v = EXCLUDED.v, n = EXCLUDED.n, fetched_at = EXCLUDED.fetched_at"""),
            {"src": SOURCE, "coin": coin, "iv": interval, "step": step, "a": start, "z": end})


async def import_binance(sf: sessionmaker[Session], settings: Settings, max_files: int | None = None) -> dict:
    t0 = time.monotonic()
    today = datetime.now(UTC).date()
    with sf() as s:
        run = IngestRun(kind="binance", status="running", stats={})
        s.add(run)
        s.commit()
        run_id = run.id
        wanted = coins_to_import(s, settings)
        known = {r.coin: r for r in s.execute(text(
            "SELECT coin, symbol, multiplier, status, checked_at FROM market_symbol WHERE source = :s"),
            {"s": SOURCE}).all()}
        done = set(s.execute(text("SELECT path FROM market_file WHERE source = :s"), {"s": SOURCE}).scalars())

    stats: dict = {"coins": {}, "validated": {}, "files": 0, "rows": 0, "missing": 0, "errors": []}
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        # 1. symbol map (re-validate weekly)
        for coin in wanted:
            k = known.get(coin)
            if k and k.checked_at > datetime.now(UTC) - timedelta(days=7):
                continue
            try:
                with sf() as s:
                    rec = await validate_symbol(client, s, coin, today)
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"validate {coin}: {exc!r}"[:300])
                continue
            with sf() as s:
                s.execute(text("""
                    INSERT INTO market_symbol (coin, source, symbol, multiplier, status, median_ratio, basis_p95_bps,
                                               n_samples, checked_at, notes)
                    VALUES (:coin, :source, :symbol, :multiplier, :status, :median_ratio, :basis_p95_bps, :n_samples,
                            now(), :notes)
                    ON CONFLICT (coin, source) DO UPDATE SET symbol = EXCLUDED.symbol, multiplier = EXCLUDED.multiplier,
                      status = EXCLUDED.status, median_ratio = EXCLUDED.median_ratio,
                      basis_p95_bps = EXCLUDED.basis_p95_bps, n_samples = EXCLUDED.n_samples,
                      checked_at = EXCLUDED.checked_at, notes = EXCLUDED.notes"""), rec)
                s.commit()
            stats["validated"][coin] = rec["status"]

        with sf() as s:
            valid = {r.coin: r.symbol for r in s.execute(text(
                "SELECT coin, symbol FROM market_symbol WHERE source = :s AND status = 'valid'"), {"s": SOURCE}).all()}

        # 2. files
        jobs = []
        for coin, start in wanted.items():
            if coin in valid:
                for path, period, kind in plan_files(valid[coin], start, today, done):
                    jobs.append((coin, path, period, kind))
        if max_files is not None:
            jobs = jobs[:max_files]
        months_done: set[tuple[str, str]] = set()

        async def one(coin: str, path: str, period: str, kind: str) -> None:
            if kind == "daily" and (coin, period[:7]) in months_done:
                return  # the monthly file of that month was imported in this run
            async with sem:
                try:
                    got = await fetch_verified(client, path)
                except (httpx.HTTPError, ChecksumError) as exc:
                    stats["errors"].append(f"{path}: {exc!r}"[:300])
                    return
            if got is None:
                stats["missing"] += 1  # not published yet (recent month/day)
                return
            body, sha = got
            klines = await asyncio.to_thread(parse_klines, body)
            now = datetime.now(UTC)
            with sf() as s:
                n = store_klines(s, coin, klines, now)
                if klines:
                    aggregate(s, coin, klines[0].t.replace(hour=0, minute=0), klines[-1].t + timedelta(minutes=1))
                s.execute(text("""
                    INSERT INTO market_file (source, path, coin, interval, period, sha256, rows, imported_at)
                    VALUES (:s, :p, :c, '1m', :per, :sha, :n, :now) ON CONFLICT DO NOTHING"""),
                    {"s": SOURCE, "p": path, "c": coin, "per": period, "sha": sha, "n": n, "now": now})
                s.commit()
            if kind == "monthly":
                months_done.add((coin, period))
            stats["files"] += 1
            stats["rows"] += n
            stats["coins"][coin] = stats["coins"].get(coin, 0) + n

        # monthly files first so that daily files of the same month can be skipped
        await asyncio.gather(*(one(*j) for j in jobs if j[3] == "monthly"))
        await asyncio.gather(*(one(*j) for j in jobs if j[3] == "daily"))

    stats["planned"] = len(jobs)
    stats["duration_s"] = round(time.monotonic() - t0, 2)
    status = "success" if not stats["errors"] else ("partial" if stats["files"] else "failed")
    with sf() as s:
        r = s.get(IngestRun, run_id)
        r.status, r.finished_at, r.stats = status, datetime.now(UTC), stats
        s.commit()
    return {"run_id": run_id, "status": status, **stats}

