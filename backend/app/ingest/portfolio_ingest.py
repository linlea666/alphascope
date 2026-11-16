"""Budgeted enrichment of Tier B wallets: `portfolio` (equity + flow-adjusted PnL curves) and
`clearinghouseState` (positions). Every response is archived raw; metrics are recomputed from points."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import AccountSnapshot, IngestRun, PositionSnapshot, RawObject, Wallet, WalletMetric
from app.quant.quality import AGGREGATE_ACCOUNT
from app.quant.returns import METRIC_VERSION, METRIC_WINDOWS, Point, compute_series_metrics
from app.sources.hl_client import HyperliquidInfoClient
from app.sources.raw_store import RawStore

log = logging.getLogger(__name__)

# Only the account-level series are stored; perp* variants stay in the raw archive.
STORED_SERIES = ("day", "week", "month", "allTime")
# Leaderboard value this many times above the address's own equity (portfolio, perp + spot) => the
# leaderboard row aggregates sub-accounts, so per-address metrics describe only part of the entity.
AGGREGATE_RATIO = 2.0


def select_enrichment_batch(session: Session, limit: int, refresh_days: int, now: datetime) -> list[tuple[int, str]]:
    """Tier B wallets never enriched first, then the stalest; ordered by 30d volume (activity, not ROI)."""
    stale = now - timedelta(days=refresh_days)
    rows = session.execute(
        select(Wallet.id, Wallet.address)
        .where(Wallet.present_in_latest, Wallet.tier == "B")
        .where((Wallet.portfolio_fetched_at.is_(None)) | (Wallet.portfolio_fetched_at < stale))
        .order_by(Wallet.portfolio_fetched_at.asc().nulls_first(), Wallet.month_vlm.desc().nulls_last())
        .limit(limit)
    ).all()
    return [(r.id, r.address) for r in rows]


def _archive(session: Session, store: RawStore, run_id: int, source: str, payload: dict, body: bytes,
             fetched_at: datetime) -> int:
    key = f"{payload.get('user', 'x')}-{fetched_at.strftime('%H%M%S%f')}"
    stored = store.put(source, key, fetched_at, body)
    raw = RawObject(source=source, request=payload, fetched_at=fetched_at, storage_path=stored.path,
                    sha256=stored.sha256, size_bytes=stored.size_bytes, ingest_run_id=run_id)
    session.add(raw)
    session.flush()
    return raw.id


def _ms(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000, tz=UTC)


def parse_portfolio(data: list) -> dict[str, list[Point]]:
    out: dict[str, list[Point]] = {}
    for name, series in data:
        av = {int(t): float(v) for t, v in series.get("accountValueHistory", [])}
        pnl = {int(t): float(v) for t, v in series.get("pnlHistory", [])}
        out[name] = [Point(_ms(t), av[t], pnl[t]) for t in sorted(av.keys() & pnl.keys())]
    return out


def store_portfolio(session: Session, wallet_id: int, series: dict[str, list[Point]], fetched_at: datetime,
                    raw_id: int, now: datetime) -> float | None:
    """Store points + recompute metrics; returns the address's latest own account value (perp + spot)."""
    rows = [
        {"w": wallet_id, "s": name, "ts": p.ts, "av": p.account_value, "pnl": p.pnl, "f": fetched_at, "r": raw_id}
        for name in STORED_SERIES for p in series.get(name, [])
    ]
    if rows:
        session.execute(text("""
            INSERT INTO equity_point (wallet_id, series, ts, account_value, pnl, fetched_at, raw_object_id)
            VALUES (:w, :s, :ts, :av, :pnl, :f, :r) ON CONFLICT DO NOTHING"""), rows)
    for window, (series_name, lookback) in METRIC_WINDOWS.items():
        m = compute_series_metrics(series.get(series_name, []), lookback, now)
        session.merge(WalletMetric(
            wallet_id=wallet_id, metric_window=window, metric_version=METRIC_VERSION, computed_at=now,
            source_series=series_name, covered=m.covered, span_start=m.span_start, span_end=m.span_end,
            n_points=m.n_points, n_valid_periods=m.n_valid_periods, median_step_hours=m.median_step_hours,
            twr=m.twr, mdd=m.mdd, pnl=m.pnl, pnl_mdd_usd=m.pnl_mdd_usd, net_flows=m.net_flows,
            positive_period_ratio=m.positive_period_ratio, reliability=m.reliability, notes=m.notes))
    latest = series.get("allTime") or series.get("day") or []
    return latest[-1].account_value if latest else None


def store_clearinghouse(session: Session, wallet_id: int, data: dict, fetched_at: datetime, raw_id: int) -> Decimal:
    ms = data["marginSummary"]
    snap = AccountSnapshot(wallet_id=wallet_id, fetched_at=fetched_at, account_value=Decimal(ms["accountValue"]),
                           total_ntl_pos=Decimal(ms["totalNtlPos"]), total_margin_used=Decimal(ms["totalMarginUsed"]),
                           withdrawable=Decimal(data["withdrawable"]) if data.get("withdrawable") else None,
                           raw_object_id=raw_id)
    session.add(snap)
    session.flush()
    for ap in data.get("assetPositions", []):
        p = ap["position"]
        lev = p.get("leverage") or {}
        session.add(PositionSnapshot(
            account_snapshot_id=snap.id, coin=p["coin"], szi=Decimal(p["szi"]),
            entry_px=Decimal(p["entryPx"]) if p.get("entryPx") else None,
            position_value=Decimal(p["positionValue"]) if p.get("positionValue") else None,
            unrealized_pnl=Decimal(p["unrealizedPnl"]) if p.get("unrealizedPnl") else None,
            return_on_equity=Decimal(p["returnOnEquity"]) if p.get("returnOnEquity") else None,
            leverage_type=lev.get("type"), leverage_value=lev.get("value"),
            liquidation_px=Decimal(p["liquidationPx"]) if p.get("liquidationPx") else None,
            margin_used=Decimal(p["marginUsed"]) if p.get("marginUsed") else None))
    return snap.account_value


def _aggregate_flag(current: list[str], leaderboard_av: Decimal | None, own_av: Decimal) -> list[str]:
    flags = [f for f in current if f != AGGREGATE_ACCOUNT]
    if leaderboard_av and leaderboard_av > 1000 and leaderboard_av > own_av * Decimal(AGGREGATE_RATIO):
        flags.append(AGGREGATE_ACCOUNT)
    return flags


async def _fetch_wallet(client: HyperliquidInfoClient, address: str) -> tuple[dict, bytes, dict, bytes]:
    pf_payload = {"type": "portfolio", "user": address}
    ch_payload = {"type": "clearinghouseState", "user": address}
    pf, ch = await asyncio.gather(client.info(pf_payload), client.info(ch_payload))
    return pf_payload, pf, ch_payload, ch


async def enrich_batch(sf: sessionmaker[Session], settings: Settings, store: RawStore, limit: int | None = None,
                       addresses: list[str] | None = None) -> dict:
    t0 = time.monotonic()
    now = datetime.now(UTC)
    limit = limit or settings.enrich_batch_size
    with sf() as session:
        run = IngestRun(kind="enrich", status="running", stats={})
        session.add(run)
        session.commit()
        if addresses:
            batch = [(r.id, r.address) for r in session.execute(
                select(Wallet.id, Wallet.address).where(Wallet.address.in_([a.lower() for a in addresses]))).all()]
        else:
            batch = select_enrichment_batch(session, limit, settings.portfolio_refresh_days, now)
        run_id = run.id

    budget = settings.hl_weight_per_minute * settings.hl_weight_safety
    ok = failed = 0
    errors: list[str] = []
    sem = asyncio.Semaphore(settings.enrich_concurrency)
    async with HyperliquidInfoClient(settings.hl_info_url, budget, settings.hl_timeout_seconds,
                                     settings.hl_max_retries) as client:

        async def one(wallet_id: int, address: str) -> None:
            nonlocal ok, failed
            async with sem:
                try:
                    pf_payload, pf, ch_payload, ch = await _fetch_wallet(client, address)
                except Exception as exc:  # noqa: BLE001 — one wallet must not stop the batch
                    failed += 1
                    errors.append(f"{address}: {exc!r}"[:300])
                    return
            fetched_at = datetime.now(UTC)
            with sf() as s:
                try:
                    pf_raw = _archive(s, store, run_id, "portfolio", pf_payload, pf.body, fetched_at)
                    pf_av = store_portfolio(s, wallet_id, parse_portfolio(pf.data), fetched_at, pf_raw, fetched_at)
                    ch_raw = _archive(s, store, run_id, "clearinghouseState", ch_payload, ch.body, fetched_at)
                    ch_av = store_clearinghouse(s, wallet_id, ch.data, fetched_at, ch_raw)
                    own_av = Decimal(str(pf_av)) if pf_av is not None else ch_av
                    w = s.get(Wallet, wallet_id)
                    w.enrichment_flags = _aggregate_flag(list(w.enrichment_flags or []), w.account_value, own_av)
                    w.portfolio_fetched_at = w.clearinghouse_fetched_at = fetched_at
                    s.commit()
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    s.rollback()
                    failed += 1
                    errors.append(f"{address}: {exc!r}"[:300])

        await asyncio.gather(*(one(wid, addr) for wid, addr in batch))

        stats = {"selected": len(batch), "ok": ok, "failed": failed, "errors": errors[:20],
                 "api": client.usage.as_dict(), "budget_per_minute": budget,
                 "duration_s": round(time.monotonic() - t0, 2)}
    status = "success" if failed == 0 else ("partial" if ok else "failed")
    with sf() as session:
        run = session.get(IngestRun, run_id)
        run.status, run.finished_at, run.stats = status, datetime.now(UTC), json.loads(json.dumps(stats))
        session.commit()
    return {"run_id": run_id, "status": status, **stats}
