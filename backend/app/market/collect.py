"""Polling for the market radar: who is watched (position_watch) and the due-trader sweeps for both venues.

Hyperliquid: one clearinghouseState (weight 2) per due wallet, from its own request budget, plus one allMids per run.
OKX: one position-current per due trader plus one tickers call per run, from the worker's OKX budget.
"""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.alerts.diff import parse_clearinghouse
from app.market.observe import record_failure, record_observation
from app.okx.client import OkxClient
from app.okx.live import load_specs, mids_from_tickers, parse_current
from app.okx.positions import parse_inst
from app.sources.hl_client import HyperliquidInfoClient

log = logging.getLogger(__name__)

# Traders always polled at the hot cadence: watched, followed, subscribed or with significant timing scores
PRIORITY_SQL = """
    SELECT wallet_id FROM watch_item
    UNION SELECT leader_wallet_id FROM copy_sim WHERE status <> 'stopped'
    UNION SELECT wallet_id FROM alert_subscription WHERE enabled
    UNION SELECT wallet_id FROM timing_score WHERE status = 'scored' AND q_value < 0.1"""


def sync_watch(s: Session, now: datetime) -> dict:
    """Bring position_watch in line with who should be polled; new traders start due within the next 30 minutes."""
    wanted = s.execute(text(f"""
        WITH pri AS ({PRIORITY_SQL})
        SELECT w.id, w.venue,
               CASE WHEN w.id IN (SELECT wallet_id FROM pri) THEN 'hot'
                    WHEN w.venue = 'okx' AND t.pool_status IN ('active', 'watching') THEN 'hot'
                    ELSE 'cold' END AS base
        FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id
        WHERE (w.venue = 'hyperliquid' AND ((w.present_in_latest AND w.tier = 'B') OR w.id IN (SELECT wallet_id FROM pri)))
           OR (w.venue = 'okx' AND w.handle IS NOT NULL
               AND (t.pool_status IN ('active', 'watching') OR t.newest_at > :recent
                    OR w.id IN (SELECT wallet_id FROM pri)))"""),
        {"recent": now - timedelta(days=30)}).all()
    ids = [r.id for r in wanted]
    removed = s.execute(text("DELETE FROM position_watch WHERE NOT (wallet_id = ANY(:ids)) RETURNING wallet_id"),
                        {"ids": ids}).scalars().all()
    if removed:  # no longer polled: drop their current rows instead of leaving stale positions around
        s.execute(text("DELETE FROM trader_position WHERE wallet_id = ANY(:ids)"), {"ids": list(removed)})
    added = s.execute(text("""
        INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due, failures, n_positions)
        SELECT x.id, x.venue, x.base, x.base, :now, :now + x.jitter * interval '1 second', 0, 0
        FROM unnest(CAST(:ids AS bigint[]), CAST(:venues AS text[]), CAST(:bases AS text[]),
                    CAST(:jitter AS int[])) AS x(id, venue, base, jitter)
        ON CONFLICT (wallet_id) DO UPDATE SET base_tier = EXCLUDED.base_tier,
          tier = CASE WHEN EXCLUDED.base_tier = 'hot' THEN 'hot' ELSE position_watch.tier END
        RETURNING (xmax = 0) AS inserted"""),
        {"now": now, "ids": ids, "venues": [r.venue for r in wanted], "bases": [r.base for r in wanted],
         "jitter": [random.randint(0, 1800) for _ in wanted]}).scalars().all()
    return {"watched": len(ids), "added": sum(1 for a in added if a), "removed": len(removed)}


def _due(s: Session, venue: str, now: datetime, limit: int) -> list:
    return s.execute(text("""
        SELECT p.wallet_id, w.address, w.handle FROM position_watch p JOIN wallet w ON w.id = p.wallet_id
        WHERE p.venue = :v AND p.next_due <= :now ORDER BY (p.tier = 'hot') DESC, p.next_due LIMIT :n"""),
        {"v": venue, "now": now, "n": limit}).all()


async def sweep_hyperliquid(sf: sessionmaker[Session], client: HyperliquidInfoClient, limit: int = 1500,
                            concurrency: int = 6, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        due = _due(s, "hyperliquid", now, limit)
    if not due:
        return {"due": 0, "ok": 0, "failed": 0, "events": 0}
    mids = {k: Decimal(v) for k, v in (await client.info({"type": "allMids"})).data.items() if not k.startswith("@")}
    sem = asyncio.Semaphore(concurrency)
    out = {"due": len(due), "ok": 0, "failed": 0, "events": 0}

    async def one(wid: int, address: str) -> None:
        async with sem:
            try:
                r = await client.info({"type": "clearinghouseState", "user": address})
            except Exception as exc:  # noqa: BLE001 — one wallet must not stop the sweep
                with sf() as s:
                    record_failure(s, wid, datetime.now(UTC), repr(exc))
                    s.commit()
                out["failed"] += 1
                return
        positions, av, _ = parse_clearinghouse(r.data)
        with sf() as s:
            out["events"] += len(record_observation(s, wid, "hyperliquid", positions, av, datetime.now(UTC), mids))
            s.commit()
        out["ok"] += 1

    await asyncio.gather(*(one(wid, addr) for wid, addr, _ in due))
    return out


def okx_open_times(raw: list[dict]) -> dict[str, datetime]:
    """Earliest venue-reported open time (cTime) per coin among legs on the net side."""
    legs: dict[str, list[tuple[int, Decimal, int]]] = {}
    for p in raw:
        try:
            coin, _ = parse_inst(str(p.get("instId") or ""))
            pos, c_time = Decimal(str(p.get("pos") or 0)), int(p.get("cTime") or 0)
        except (ValueError, ArithmeticError):
            continue
        side = p.get("posSide")
        sign = 1 if side == "long" else -1 if side == "short" else (pos > 0) - (pos < 0)
        if c_time and sign:
            legs.setdefault(coin, []).append((sign, abs(pos), c_time))
    out = {}
    for coin, ls in legs.items():
        net = sum(s * size for s, size, _ in ls)  # contracts of one instrument; enough to know the net side
        same = [t for s, _, t in ls if s == (1 if net > 0 else -1)] or [t for _, _, t in ls]
        out[coin] = datetime.fromtimestamp(min(same) / 1000, UTC)
    return out


async def sweep_okx(sf: sessionmaker[Session], client: OkxClient, limit: int = 300,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        due = _due(s, "okx", now, limit)
        specs, _ = load_specs(s)
    if not due:
        return {"due": 0, "ok": 0, "failed": 0, "events": 0}
    mids = mids_from_tickers(await client.tickers())
    out = {"due": len(due), "ok": 0, "failed": 0, "events": 0}
    with sf() as s:
        assets = dict(s.execute(text("SELECT wallet_id, asset FROM okx_trader WHERE wallet_id = ANY(:ids)"),
                                {"ids": [d.wallet_id for d in due]}).all())
    for wid, _, handle in due:
        try:
            raw = await client.current(handle)
        except Exception as exc:  # noqa: BLE001
            with sf() as s:
                record_failure(s, wid, datetime.now(UTC), repr(exc))
                s.commit()
            out["failed"] += 1
            continue
        positions, _ = parse_current(raw, specs)
        with sf() as s:
            out["events"] += len(record_observation(s, wid, "okx", positions, assets.get(wid), datetime.now(UTC),
                                                    mids, okx_open_times(raw)))
            s.commit()
        out["ok"] += 1
    return out
