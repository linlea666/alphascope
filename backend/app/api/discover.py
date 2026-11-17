"""Multi-style discovery: style boards with evidence, a trader's ability profile, and the week's / month's best
performers with sample gates (docs/13)."""

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import num, venue_clause
from app.db import get_session
from app.factors.styles import BY_KEY, OOS_DAYS, STYLES
from app.quant.quality import AGGREGATE_ACCOUNT, DEFAULT_RANKING_EXCLUSIONS
from app.traders import parse_trader_key

router = APIRouter(prefix="/api/v1/discover", tags=["discover"])
STATUS_ORDER = {"verified": 0, "candidate": 1, "watch": 2}
# the factors shown next to every trader on a style board
HEADLINE = ("n_closed", "net_pnl", "win_rate_lo", "profit_factor", "positive_month_ratio", "dd_ratio",
            "open_loss_ratio", "liq_rate", "median_leverage", "days_since_last")


def _factors(session: Session, ids: list[int], names: tuple[str, ...], window: str = "all") -> dict[int, dict]:
    out: dict[int, dict] = {}
    for wid, factor, value, n in session.execute(text("""
        SELECT wallet_id, factor, value, n FROM factor_value
        WHERE wallet_id = ANY(:ids) AND factor = ANY(:f) AND time_window = :w"""),
            {"ids": ids, "f": list(names), "w": window}).all():
        out.setdefault(wid, {})[factor] = {"value": num(value), "n": n}
    return out


@router.get("/styles")
def styles(session: Session = Depends(get_session), venue: Literal["all", "hyperliquid", "okx"] = "all"):
    counts: dict[str, dict[str, int]] = {}
    for style, status, n in session.execute(text(f"""
        SELECT t.style, t.status, count(*) FROM style_tag t JOIN wallet w ON w.id = t.wallet_id
        WHERE true {venue_clause(venue)} GROUP BY 1, 2""")).all():
        counts.setdefault(style, {})[status] = n
    computed = session.execute(text("SELECT max(computed_at) FROM style_tag")).scalar()
    return {"computed_at": computed, "oos_days": OOS_DAYS,
            "styles": [{"key": s.key, "risk_flag": s.risk_flag, "note": s.note, "oos": bool(s.forward),
                        "rules": [r if not isinstance(r, dict) else {"any": r["any"]} for r in s.rules],
                        "requires": s.requires, "counts": counts.get(s.key, {})} for s in STYLES]}


@router.get("")
def board(session: Session = Depends(get_session), style: str = "steady",
          venue: Literal["all", "hyperliquid", "okx"] = "all",
          status: Literal["verified", "candidate", "watch"] = "candidate",
          sort: Literal["score", "confidence", "net_pnl"] = "score", limit: int = Query(100, ge=1, le=500)):
    """Traders labelled with a style at `status` or better (verified ⊃ candidate ⊃ watch), with their evidence."""
    if style not in BY_KEY:
        raise HTTPException(422, f"style must be one of {sorted(BY_KEY)}")
    allowed = [k for k, v in STATUS_ORDER.items() if v <= STATUS_ORDER[status]]
    order = {"score": "t.score DESC NULLS LAST", "confidence": "t.confidence DESC",
             "net_pnl": "(SELECT value FROM factor_value f WHERE f.wallet_id = t.wallet_id AND f.factor = 'net_pnl' "
                        "AND f.time_window = 'all') DESC NULLS LAST"}[sort]
    rows = session.execute(text(f"""
        SELECT t.wallet_id, w.address, w.display_name, w.venue, t.status, t.score, t.confidence, t.n, t.evidence,
               t.oos, t.computed_at,
               (SELECT array_agg(o.style ORDER BY o.style) FROM style_tag o
                WHERE o.wallet_id = t.wallet_id AND o.style <> t.style AND o.status IN ('verified', 'candidate')) AS other
        FROM style_tag t JOIN wallet w ON w.id = t.wallet_id
        WHERE t.style = :style AND t.status = ANY(:st) {venue_clause(venue)}
        ORDER BY array_position(ARRAY['verified', 'candidate', 'watch'], t.status::text), {order}
        LIMIT :n"""), {"style": style, "st": allowed, "n": limit}).mappings().all()
    facts = _factors(session, [r["wallet_id"] for r in rows], HEADLINE)
    return {"style": style, "status": status, "venue": venue,
            "rows": [{**{k: v for k, v in dict(r).items() if k != "wallet_id"}, "score": num(r["score"]),
                      "confidence": num(r["confidence"]), "other": list(r["other"] or []),
                      "factors": facts.get(r["wallet_id"], {})} for r in rows]}


@router.get("/profile/{address}")
def profile(address: str, session: Session = Depends(get_session)):
    """A trader's factor values (full history and last 90 days) and style labels with evidence."""
    key = parse_trader_key(address)
    if not key:
        raise HTTPException(422, "invalid address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": key}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    factors: dict[str, dict] = {}
    for factor, window, value, n, as_of in session.execute(text("""
        SELECT factor, time_window, value, n, as_of FROM factor_value WHERE wallet_id = :w"""), {"w": wid}).all():
        factors.setdefault(factor, {})[window] = {"value": num(value), "n": n, "as_of": as_of}
    tags = session.execute(text("""
        SELECT style, status, score, confidence, n, evidence, oos, computed_at FROM style_tag WHERE wallet_id = :w
        ORDER BY array_position(ARRAY['verified', 'candidate', 'watch'], status::text), style"""),
        {"w": wid}).mappings().all()
    return {"factors": factors,
            "styles": [{**dict(t), "score": num(t["score"]), "confidence": num(t["confidence"])} for t in tags]}


@router.get("/top")
def top_performers(session: Session = Depends(get_session), period: Literal["7d", "30d"] = "7d",
                   limit: int = Query(10, ge=1, le=50)):
    """The period's best performers per venue, after sample and risk gates. Short-term results, not proof of skill.

    OKX: realized PnL of positions closed in the period over lead-trading assets; at least 5 closed positions, no
    liquidation in the period, assets >= $1k, and not holding an open loss above 30% of assets at the last poll.
    Hyperliquid: the official leaderboard's week / month PnL over current equity (the official ROI uses small starting
    balances), accounts >= $10k without noise flags, PnL >= $1k, and a known 30-day drawdown no worse than -50%.
    """
    days = 7 if period == "7d" else 30
    since = datetime.now(UTC) - timedelta(days=days)
    okx = session.execute(text("""
        WITH p AS (
          SELECT wallet_id, count(*) AS n, sum(realized_pnl) AS net, count(*) FILTER (WHERE liquidated) AS liq,
                 count(*) FILTER (WHERE realized_pnl > 0) AS wins
          FROM okx_position WHERE close_time > :since AND close_type <> 'partClose' GROUP BY wallet_id),
        base AS (
          SELECT w.id, w.address, w.display_name, coalesce(t.asset, (
                   SELECT r.asset FROM okx_leaderboard_row r JOIN okx_leaderboard_run u ON u.id = r.run_id
                   WHERE r.wallet_id = w.id ORDER BY u.business_date DESC LIMIT 1)) AS asset
          FROM wallet w JOIN okx_trader t ON t.wallet_id = w.id)
        SELECT b.address, b.display_name, b.asset, p.n, p.net, p.wins, p.net / b.asset AS ret,
               (SELECT coalesce(sum(upl), 0) FROM trader_position tp WHERE tp.wallet_id = b.id) AS open_upl,
               (SELECT max(last_observed_at) FROM position_watch pw WHERE pw.wallet_id = b.id) AS polled,
               (SELECT array_agg(style ORDER BY style) FROM style_tag s WHERE s.wallet_id = b.id
                  AND s.status IN ('verified', 'candidate')) AS styles
        FROM p JOIN base b ON b.id = p.wallet_id
        WHERE p.n >= 5 AND p.liq = 0 AND b.asset >= 1000 AND p.net > 0
        ORDER BY p.net / b.asset DESC LIMIT :n"""), {"since": since, "n": limit * 3}).mappings().all()
    okx_rows = []
    for r in okx:
        polled = r["polled"] is not None
        open_loss = max(0.0, -float(r["open_upl"] or 0)) / float(r["asset"]) if polled else None
        if open_loss is not None and open_loss > 0.3:
            continue  # realized gains while sitting on a large open loss
        okx_rows.append({**dict(r), "asset": num(r["asset"]), "net": num(r["net"]), "ret": num(r["ret"]),
                         "open_loss_ratio": open_loss, "styles": list(r["styles"] or []), "venue": "okx"})
    window = "week" if period == "7d" else "month"
    excluded = [*DEFAULT_RANKING_EXCLUSIONS, AGGREGATE_ACCOUNT]
    hl = session.execute(text(f"""
        SELECT w.address, w.display_name, w.account_value AS asset, w.{window}_pnl AS net,
               w.{window}_pnl / w.account_value AS ret, m.mdd, m.reliability,
               (SELECT array_agg(style ORDER BY style) FROM style_tag s WHERE s.wallet_id = w.id
                  AND s.status IN ('verified', 'candidate')) AS styles
        FROM wallet w LEFT JOIN wallet_metric m ON m.wallet_id = w.id AND m.metric_window = '30d'
        WHERE w.venue = 'hyperliquid' AND w.present_in_latest AND w.account_value >= 10000
          AND w.{window}_pnl >= 1000 AND w.{window}_vlm > 0 AND (m.mdd IS NULL OR m.mdd >= -0.5)
          AND NOT (w.quality_flags && CAST(:ex AS text[]) OR w.enrichment_flags && CAST(:ex AS text[]))
        ORDER BY w.{window}_pnl / w.account_value DESC LIMIT :n"""), {"ex": excluded, "n": limit}).mappings().all()
    return {
        "period": period, "since": since,
        "okx": okx_rows[:limit],
        "hyperliquid": [{**dict(r), "asset": num(r["asset"]), "net": num(r["net"]), "ret": num(r["ret"]),
                         "mdd": num(r["mdd"]), "styles": list(r["styles"] or []), "venue": "hyperliquid"} for r in hl],
    }


# Style dimensions for "similar traders": how someone trades, not how much they made. Heavy-tailed ones are logged.
SIMILAR_FACTORS = ("median_leverage", "short_share", "scalp_share", "top_coin_share", "hold_win_h", "hold_loss_h",
                   "win_rate", "profit_factor", "dd_ratio", "coin_hhi")
LOGGED = {"median_leverage", "hold_win_h", "hold_loss_h", "profit_factor"}
SIMILAR_MIN_CLOSED = 15
SIMILAR_MIN_SHARED = 6


@router.get("/similar/{address}")
def similar(address: str, session: Session = Depends(get_session), limit: int = Query(6, ge=1, le=20),
            venue: Literal["all", "hyperliquid", "okx"] = "all"):
    """Traders whose style factors are closest (standardised distance over the dimensions both have).
    Only traders with at least SIMILAR_MIN_CLOSED closed cycles take part."""
    key = parse_trader_key(address)
    if not key:
        raise HTTPException(422, "invalid address")
    me = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": key}).scalar()
    if me is None:
        raise HTTPException(404, "wallet not in database")
    rows = session.execute(text(f"""
        SELECT f.wallet_id, f.factor, f.value FROM factor_value f JOIN wallet w ON w.id = f.wallet_id
        WHERE f.time_window = 'all' AND f.factor = ANY(:f) AND f.value IS NOT NULL
          AND f.wallet_id IN (SELECT wallet_id FROM factor_value WHERE factor = 'n_closed' AND time_window = 'all'
                              AND value >= :m){venue_clause(venue)}"""),
        {"f": list(SIMILAR_FACTORS), "m": SIMILAR_MIN_CLOSED}).all()
    vec: dict[int, dict[str, float]] = {}
    for wid, f, v in rows:
        x = float(v)
        vec.setdefault(wid, {})[f] = math.log1p(max(x, 0)) if f in LOGGED else x
    if me not in vec:
        mine = session.execute(text(f"""
            SELECT factor, value FROM factor_value WHERE wallet_id = :w AND time_window = 'all'
              AND factor = ANY(:f) AND value IS NOT NULL"""), {"w": me, "f": list(SIMILAR_FACTORS)}).all()
        vec[me] = {f: (math.log1p(max(float(v), 0)) if f in LOGGED else float(v)) for f, v in mine}
    stats = {}
    for f in SIMILAR_FACTORS:
        xs = [v[f] for v in vec.values() if f in v]
        if len(xs) > 2:
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
            stats[f] = (m, sd)
    target = vec[me]
    scored = []
    for wid, v in vec.items():
        if wid == me:
            continue
        shared = [f for f in stats if f in v and f in target]
        if len(shared) < SIMILAR_MIN_SHARED:
            continue
        diffs = {f: abs(v[f] - target[f]) / stats[f][1] for f in shared}
        d = math.sqrt(sum(x * x for x in diffs.values()) / len(shared))
        scored.append((d, wid, sorted(diffs, key=diffs.get)[:3]))
    scored.sort()
    top = scored[:limit]
    if not top:
        return {"rows": [], "factors": list(SIMILAR_FACTORS), "min_closed": SIMILAR_MIN_CLOSED}
    meta = {r["id"]: r for r in session.execute(text("""
        SELECT w.id, w.address, w.display_name, w.venue,
               (SELECT value FROM factor_value f WHERE f.wallet_id = w.id AND f.factor = 'net_pnl' AND f.time_window = 'all') AS net_pnl,
               (SELECT value FROM factor_value f WHERE f.wallet_id = w.id AND f.factor = 'n_closed' AND f.time_window = 'all') AS n_closed,
               (SELECT array_agg(style ORDER BY style) FROM style_tag s WHERE s.wallet_id = w.id
                  AND s.status IN ('verified', 'candidate')) AS styles
        FROM wallet w WHERE w.id = ANY(:ids)"""), {"ids": [w for _, w, _ in top]}).mappings().all()}
    return {"factors": list(SIMILAR_FACTORS), "min_closed": SIMILAR_MIN_CLOSED, "rows": [
        {"address": meta[w]["address"], "display_name": meta[w]["display_name"], "venue": meta[w]["venue"],
         "similarity": 1 / (1 + d), "closest": close, "net_pnl": num(meta[w]["net_pnl"]),
         "n_closed": int(meta[w]["n_closed"] or 0), "styles": list(meta[w]["styles"] or [])}
        for d, w, close in top if w in meta]}
