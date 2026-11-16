import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import baseline_import_at, data_as_of, num
from app.db import get_session
from app.models import LEADERBOARD_WINDOWS
from app.sources.leaderboard import ADDRESS_RE

router = APIRouter(prefix="/api/v1/wallets", tags=["wallets"])


@router.get("/new")
def new_wallets(session: Session = Depends(get_session), days: int = Query(7, ge=1, le=90),
                limit: int = Query(50, ge=1, le=200)):
    """Wallets first seen after the initial bulk import (true discoveries, not the seed set)."""
    baseline = baseline_import_at(session)
    rows = session.execute(text("""
        SELECT address, display_name, first_seen_at, account_value, month_pnl, month_vlm, tier, quality_flags
        FROM wallet
        WHERE first_seen_at > :baseline AND first_seen_at > now() - make_interval(days => :days)
        ORDER BY first_seen_at DESC, month_vlm DESC NULLS LAST
        LIMIT :limit"""), {"baseline": baseline, "days": days, "limit": limit}).all()
    return {
        "baseline_import_at": baseline,
        "freshness": data_as_of(session),
        "rows": [{"address": r.address, "display_name": r.display_name, "first_seen_at": r.first_seen_at,
                  "account_value": num(r.account_value), "month_pnl": num(r.month_pnl),
                  "month_vlm": num(r.month_vlm), "tier": r.tier, "flags": list(r.quality_flags or [])}
                 for r in rows],
    }


@router.get("/{address}")
def wallet_detail(address: str, session: Session = Depends(get_session)):
    address = address.lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(422, "invalid address")
    w = session.execute(text("SELECT * FROM wallet WHERE address = :a"), {"a": address}).mappings().first()
    if w is None:
        raise HTTPException(404, "wallet not in database")
    wid = w["id"]

    metrics = session.execute(text("""
        SELECT * FROM wallet_metric WHERE wallet_id = :w
        ORDER BY array_position(ARRAY['30d','90d','180d','365d','all'], metric_window)"""), {"w": wid}).mappings().all()
    equity = {}
    for series in ("month", "allTime"):
        pts = session.execute(text("""
            SELECT ts, account_value, pnl FROM equity_point WHERE wallet_id = :w AND series = :s ORDER BY ts"""),
            {"w": wid, "s": series}).all()
        equity[series] = [[p.ts, float(p.account_value), float(p.pnl)] for p in pts]
    snapshots = session.execute(text("""
        SELECT s.fetched_at, r.account_value, r.day_pnl, r.week_pnl, r.month_pnl, r.alltime_pnl,
               r.month_roi, r.month_vlm
        FROM leaderboard_row r JOIN leaderboard_snapshot s ON s.id = r.snapshot_id
        WHERE r.wallet_id = :w ORDER BY s.fetched_at DESC LIMIT 120"""), {"w": wid}).mappings().all()
    tiers = session.execute(text("""
        SELECT tier, previous_tier, rule_version, assigned_at FROM wallet_tier_history
        WHERE wallet_id = :w ORDER BY assigned_at DESC LIMIT 50"""), {"w": wid}).mappings().all()
    account = session.execute(text("""
        SELECT id, fetched_at, account_value, total_ntl_pos, total_margin_used, withdrawable
        FROM account_snapshot WHERE wallet_id = :w ORDER BY fetched_at DESC LIMIT 1"""), {"w": wid}).mappings().first()
    positions = []
    if account:
        positions = session.execute(text("""
            SELECT coin, szi, entry_px, position_value, unrealized_pnl, return_on_equity, leverage_type,
                   leverage_value, liquidation_px, margin_used
            FROM position_snapshot WHERE account_snapshot_id = :id ORDER BY abs(position_value) DESC NULLS LAST"""),
            {"id": account["id"]}).mappings().all()

    def clean(m) -> dict:
        return {k: (num(v) if hasattr(v, "is_finite") else v) for k, v in dict(m).items() if k not in ("id", "wallet_id")}

    return {
        "freshness": data_as_of(session),
        "wallet": {
            "address": w["address"],
            "display_name": w["display_name"],
            "first_seen_at": w["first_seen_at"],
            "first_seen_source": w["first_seen_source"],
            "last_seen_at": w["last_seen_at"],
            "present_in_latest": w["present_in_latest"],
            "removed_at": w["removed_at"],
            "account_value": num(w["account_value"]),
            "tier": w["tier"],
            "tier_rule_version": w["tier_rule_version"],
            "flags": list(w["quality_flags"] or []),
            "enrichment_flags": list(w["enrichment_flags"] or []),
            "portfolio_fetched_at": w["portfolio_fetched_at"],
            "leaderboard_fetched_at": w["leaderboard_fetched_at"],
        },
        "leaderboard": {
            win: {"pnl": num(w[f"{win}_pnl"]), "roi": num(w[f"{win}_roi"]), "vlm": num(w[f"{win}_vlm"])}
            for win in LEADERBOARD_WINDOWS
        },
        "metrics": [clean(m) for m in metrics],
        "equity": equity,
        "snapshots": [clean(s) for s in snapshots],
        "tier_history": [dict(t) for t in tiers],
        "account": clean(account) if account else None,
        "positions": [clean(p) for p in positions],
    }


SCALE_ORDER = ("scalp", "short", "swing", "macro")


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for a win rate; honest about small samples."""
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


@router.get("/{address}/positions")
def wallet_positions(address: str, session: Session = Depends(get_session), limit: int = Query(40, ge=1, le=200)):
    address = address.lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(422, "invalid address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": address}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    coverage = session.execute(text("""
        SELECT status, reason, requested_start, earliest_fill_at, latest_fill_at, n_fills, truncated, last_synced_at,
               reconstructed_at, recon_breaks FROM fill_sync WHERE wallet_id = :w"""), {"w": wid}).mappings().first()
    # A cycle still open where an incomplete sync stops is a data boundary, not a held position.
    sync_complete = bool(coverage and coverage["status"] == "synced")
    by_scale = {r.scale: r for r in session.execute(text("""
        SELECT scale, count(*) AS cycles,
               count(*) FILTER (WHERE complete AND status <> 'open') AS complete_closed,
               count(*) FILTER (WHERE complete AND status <> 'open' AND realized_pnl - fees > 0) AS wins,
               sum(realized_pnl - fees) FILTER (WHERE status <> 'open') AS net_pnl,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY holding_hours) AS median_hours,
               count(*) FILTER (WHERE status = 'open') AS open_now
        FROM position_cycle WHERE wallet_id = :w GROUP BY scale"""), {"w": wid}).all()}
    scales = []
    for sc in SCALE_ORDER:
        r = by_scale.get(sc)
        if r is None:
            scales.append({"scale": sc, "cycles": 0})
            continue
        ci = wilson(r.wins, r.complete_closed)
        scales.append({"scale": sc, "cycles": r.cycles, "complete_closed": r.complete_closed, "wins": r.wins,
                       "win_rate": r.wins / r.complete_closed if r.complete_closed else None,
                       "win_rate_ci": list(ci) if ci else None, "net_pnl": num(r.net_pnl),
                       "median_hours": num(r.median_hours), "open_now": r.open_now if sync_complete else None})
    cycles = session.execute(text("""
        SELECT coin, direction, opened_at, closed_at,
               CASE WHEN status = 'open' AND NOT :done THEN 'data_end' ELSE status END AS status,
               complete, scale, holding_hours, entry_px, exit_px,
               max_notional, n_fills, n_decisions, realized_pnl - fees AS net_pnl
        FROM position_cycle WHERE wallet_id = :w ORDER BY coalesce(closed_at, now()) DESC, opened_at DESC
        LIMIT :n"""), {"w": wid, "n": limit, "done": sync_complete}).mappings().all()
    return {
        "coverage": dict(coverage) if coverage else None,
        "sync_complete": sync_complete,
        "scales": scales,
        "cycles": [{k: (num(v) if hasattr(v, "is_finite") else v) for k, v in dict(c).items()} for c in cycles],
    }


REPLAY_INTERVALS = {"15m": ("scalp", 15), "1h": ("short", 60), "4h": ("swing", 240), "1d": ("macro", 1440)}


@router.get("/{address}/replay")
def wallet_replay(
    address: str,
    session: Session = Depends(get_session),
    coin: str | None = None,
    interval: str = "1h",
    end: str | None = Query(None, description="ISO time of the window end; default = the coin's last decision"),
    bars: int = Query(300, ge=50, le=1000),
):
    """Candles of one coin with the wallet's buy/sell decisions and the timeframe's swing pivots (K-line replay)."""
    address = address.lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(422, "invalid address")
    if interval not in REPLAY_INTERVALS:
        raise HTTPException(422, f"interval must be one of {sorted(REPLAY_INTERVALS)}")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": address}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    coins = session.execute(text("""
        SELECT coin, count(*) AS decisions, max(started_at) AS last_at FROM trade_decision WHERE wallet_id = :w
        GROUP BY coin ORDER BY count(*) DESC LIMIT 30"""), {"w": wid}).mappings().all()
    if not coins:
        return {"coins": [], "coin": None, "candles": [], "decisions": [], "pivots": []}
    coin = coin or coins[0]["coin"]
    scale, step = REPLAY_INTERVALS[interval]
    end_ts = end or session.execute(text("""
        SELECT max(started_at) FROM trade_decision WHERE wallet_id = :w AND coin = :c"""),
        {"w": wid, "c": coin}).scalar()
    if end_ts is None:
        raise HTTPException(404, "no decisions for this coin")
    params = {"c": coin, "iv": interval, "end": end_ts, "span": f"{bars * step} minutes",
              "pad": f"{step * 20} minutes"}
    candles = session.execute(text("""
        SELECT DISTINCT ON (t) t, o, h, l, c FROM market_candle
        WHERE coin = :c AND interval = :iv AND source IN ('binance_um', 'hyperliquid')
          AND t > CAST(:end AS timestamptz) - CAST(:span AS interval) AND t <= CAST(:end AS timestamptz) + CAST(:pad AS interval)
        ORDER BY t, (source = 'binance_um') DESC"""), params).all()
    if candles:
        lo_t, hi_t = candles[0][0], candles[-1][0]
    else:
        lo_t = hi_t = None
    decisions = session.execute(text("""
        SELECT d.started_at, d.side, d.kind, d.vwap, d.size, d.notional, d.pos_after, dt.location, dt.pivot_ts
        FROM trade_decision d LEFT JOIN decision_timing dt ON dt.decision_id = d.id
        WHERE d.wallet_id = :w AND d.coin = :c AND d.started_at >= :a AND d.started_at <= :b
        ORDER BY d.started_at"""), {"w": wid, "c": coin, "a": lo_t, "b": hi_t}).mappings().all() if lo_t else []
    pivots = session.execute(text("""
        SELECT kind, ts, px, leg_change, confirmed FROM timing_pivot
        WHERE coin = :c AND scale = :s AND ts >= CAST(:a AS timestamptz) - CAST(:pad AS interval)
          AND ts <= CAST(:b AS timestamptz) + CAST(:pad AS interval) ORDER BY ts"""),
        {"c": coin, "s": scale, "a": lo_t, "b": hi_t, "pad": f"{step * 60} minutes"}).mappings().all() if lo_t else []
    return {
        "coins": [dict(c) for c in coins],
        "coin": coin,
        "interval": interval,
        "scale": scale,
        "end": str(end_ts),
        "candles": [[t, float(o), float(h), float(l), float(c)] for t, o, h, l, c in candles],
        "decisions": [{**dict(d), "vwap": num(d["vwap"]), "size": num(d["size"]), "notional": num(d["notional"]),
                       "pos_after": num(d["pos_after"]), "location": num(d["location"])} for d in decisions],
        "pivots": [{**dict(p), "px": num(p["px"]), "leg_change": num(p["leg_change"])} for p in pivots],
    }
