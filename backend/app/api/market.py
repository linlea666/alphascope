"""Market positioning radar over the observed sample of traders (docs/12). Every number can be drilled into the
contributing traders and positions; nothing here describes the whole exchange."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import num
from app.db import get_session
from app.market import divergence, stats, structure
from app.market.stats import Cohort, Venue

router = APIRouter(prefix="/api/v1/market", tags=["market"])
NUMERIC = ("margin", "liq_dist", "roe", "near_long_liq_pct", "near_short_liq_pct", "long_ntl", "short_ntl", "lev10_long_ntl", "lev10_short_ntl", "lev20_long_ntl", "lev20_short_ntl",
           "liq_known_ntl", "mark_px", "median_leverage", "szi", "notional_usd", "entry_px", "liq_px", "upl",
           "account_value", "notional_change", "szi_before", "szi_after", "gross_notional")


# implied margin, distance to the reported liquidation price, and return on that margin, for a `fresh` alias f
POSITION_EXTRAS = """f.notional_usd / nullif(f.leverage, 0) AS margin,
               abs(f.mark_px - f.liq_px) / nullif(f.mark_px, 0) AS liq_dist,
               f.upl / nullif(f.notional_usd / nullif(f.leverage, 0), 0) AS roe"""
# sort key -> (expression, default direction); unknown values (no leverage, no liquidation price) always go last
POSITION_SORTS = {
    "notional": ("f.notional_usd", "desc"),
    "margin": ("f.notional_usd / nullif(f.leverage, 0)", "desc"),
    "upl": ("f.upl", "asc"),  # biggest loss first
    "liq": ("abs(f.mark_px - f.liq_px) / nullif(f.mark_px, 0)", "asc"),  # closest to liquidation first
    "leverage": ("f.leverage", "desc"),
    "opened": ("coalesce(f.opened_at, f.first_seen_at)", "desc"),
}


def position_order(sort: str, order: str | None) -> str:
    expr, default = POSITION_SORTS[sort]
    return f"{expr} {(order or default).upper()} NULLS LAST"
LIQ_TOLERANCE = 0.005  # a close within 0.5% of (or beyond) the last reported liquidation price


def _clean(d: dict) -> dict:
    return {k: (num(v) if k in NUMERIC and v is not None else v) for k, v in d.items()}


@router.get("/overview")
def overview(session: Session = Depends(get_session), venue: Venue = "all", cohort: Cohort = "all"):
    now = datetime.now(UTC)
    t = stats.totals(session, venue, cohort, now)
    ev = stats.event_counts(session, venue, cohort, now - timedelta(hours=24), now)
    return {
        "as_of": now, "venue": venue, "cohort": cohort,
        "coverage": stats.coverage(session, venue, cohort, now),
        "by_venue": {v: stats.coverage(session, v, cohort, now) for v in ("hyperliquid", "okx")},
        "totals": _clean(t),
        "opens_24h": sum(e["opens"] for e in ev.values()),
        "closes_24h": sum(e["closes"] for e in ev.values()),
    }


@router.get("/coins")
def coins(session: Session = Depends(get_session), venue: Venue = "all", cohort: Cohort = "all",
          min_traders: int = Query(2, ge=1, le=100), limit: int = Query(120, ge=1, le=500)):
    now = datetime.now(UTC)
    rows = stats.coin_rows(session, venue, cohort, now, min_traders)[:limit]
    windows = {h: stats.event_counts(session, venue, cohort, now - timedelta(hours=h), now) for h in (1, 4, 24)}
    for r in rows:
        for h, ev in windows.items():
            r[f"opens_{h}h"] = ev.get(r["coin"], {}).get("opens", 0)
    return {"as_of": now, "rows": [_clean(r) for r in rows]}


@router.get("/contributors")
def contributors(session: Session = Depends(get_session), coin: str = Query(..., max_length=48),
                 side: Literal["long", "short", "all"] = "all", venue: Venue = "all", cohort: Cohort = "all",
                 min_lev: int = Query(0, ge=0, le=200), limit: int = Query(200, ge=1, le=1000),
                 entry_min: float | None = None, entry_max: float | None = None,
                 liq_min: float | None = None, liq_max: float | None = None,
                 sort: Literal["notional", "margin", "upl", "liq", "leverage", "opened"] = "notional",
                 order: Literal["asc", "desc"] | None = None):
    """The traders and positions behind a coin's numbers (same freshness and cohort rules as the aggregates);
    entry / liquidation price ranges drill into a zone of the positioning structure.

    margin = notional / leverage: the initial margin implied by the reported leverage. For cross-margin positions the
    account's whole equity backs them, so it is an estimate of what the trader committed, not an isolated amount."""
    now = datetime.now(UTC)
    side_sql = {"long": "AND f.szi > 0", "short": "AND f.szi < 0", "all": ""}[side]
    for col, lo, hi in (("entry_px", entry_min, entry_max), ("liq_px", liq_min, liq_max)):
        if lo is not None:
            side_sql += f" AND f.{col} >= {float(lo)!r}"
        if hi is not None:
            side_sql += f" AND f.{col} < {float(hi)!r}"
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort, min_lev)}
        SELECT w.address, w.display_name, f.venue, f.szi, f.notional_usd, f.mark_px, f.entry_px, f.leverage,
               f.margin_mode, f.liq_px, f.upl, f.opened_at, f.opened_at_source, f.opened_after, f.first_seen_at,
               f.observed_at, pw.account_value, {POSITION_EXTRAS}
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id JOIN position_watch pw ON pw.wallet_id = f.wallet_id
        WHERE f.coin = :coin {side_sql}
        ORDER BY {position_order(sort, order)}, f.notional_usd DESC LIMIT :n"""),
        {"now": now, "coin": coin, "n": limit}).mappings().all()
    return {"as_of": now, "coin": coin, "sort": sort, "order": order or POSITION_SORTS[sort][1],
            "rows": [_clean(dict(r)) for r in rows]}


@router.get("/events")
def events(session: Session = Depends(get_session), hours: int = Query(24, ge=1, le=168), venue: Venue = "all",
           cohort: Cohort = "all", coin: str | None = Query(None, max_length=48),
           side: Literal["long", "short", "all"] = "all",
           kinds: str = Query("open,increase,decrease,close,flip", description="comma-separated; first_seen too"),
           min_notional: float = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    now = datetime.now(UTC)
    allowed = {"open", "increase", "decrease", "close", "flip", "first_seen"}
    kind_list = [k for k in kinds.split(",") if k in allowed]
    if not kind_list:
        raise HTTPException(422, f"kinds must be among {sorted(allowed)}")
    cond = stats.scope(venue, cohort, col="e.wallet_id", venue_col="e.venue")
    rows = session.execute(text(f"""
        SELECT e.id, w.address, w.display_name, e.venue, e.coin, e.kind, e.side, e.szi_before, e.szi_after,
               e.notional_change, e.mark_px, e.entry_px, e.leverage, e.liq_px, e.event_time, e.time_precision,
               e.window_start, e.observed_at, p.mark_px AS current_px, p.upl
        FROM market_event e JOIN wallet w ON w.id = e.wallet_id
        LEFT JOIN trader_position p ON p.wallet_id = e.wallet_id AND p.coin = e.coin
        WHERE e.event_time > :since AND e.kind = ANY(:kinds) AND e.notional_change >= :mn AND {cond}
          {"AND e.coin = :coin" if coin else ""} {"AND e.side = :side" if side != "all" else ""}
        ORDER BY e.event_time DESC, e.id DESC LIMIT :n"""),
        {"since": now - timedelta(hours=hours), "kinds": kind_list, "mn": min_notional, "coin": coin, "side": side,
         "n": limit}).mappings().all()
    numeric = NUMERIC + ("current_px",)
    return {"as_of": now, "rows": [{k: (num(v) if k in numeric and v is not None else v) for k, v in dict(r).items()}
                                   for r in rows]}


@router.get("/history")
def history(session: Session = Depends(get_session), coin: str = Query("*", max_length=48), venue: Venue = "all",
            cohort: Cohort = "all", days: int = Query(7, ge=1, le=90), as_of: datetime | None = None):
    if as_of is not None and (as_of.tzinfo is None or as_of > datetime.now(UTC)):
        raise HTTPException(422, "as_of must be a timezone-aware historical timestamp")
    rows = session.execute(text("""
        SELECT bucket_ts, available_at, observed, long_traders, short_traders, long_ntl, short_ntl, lev20_long_ntl,
               lev20_short_ntl, opens, closes
        FROM market_snapshot WHERE coin = :coin AND venue = :v AND cohort = :c AND bucket_ts > :since
          AND (CAST(:cutoff AS timestamptz) IS NULL OR available_at<=:cutoff)
        ORDER BY bucket_ts"""), {"coin": coin, "v": venue, "c": cohort,
                                 "since": (as_of or datetime.now(UTC)) - timedelta(days=days), "cutoff":as_of}).mappings().all()
    return {"coin": coin, "rows": [_clean(dict(r)) for r in rows]}


@router.get("/liquidations")
def liquidations(session: Session = Depends(get_session), coin: str = Query("BTC", max_length=48),
                 venue: Venue = "all", cohort: Cohort = "all", min_lev: int = Query(0, ge=0, le=200),
                 span_pct: float = Query(30, gt=1, le=90), bins: int = Query(40, ge=10, le=120)):
    """Venue-reported liquidation prices of fresh positions, bucketed around the current price. Positions without a
    reported liquidation price are counted separately and never placed in a bucket."""
    now = datetime.now(UTC)
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort, min_lev)}
        SELECT szi, notional_usd, liq_px, mark_px, margin_mode, wallet_id FROM fresh WHERE coin = :coin"""),
        {"now": now, "coin": coin}).all()
    marks = [float(r.mark_px) for r in rows if r.mark_px]
    if not marks:
        return {"coin": coin, "mark_px": None, "bins": [], "without_liq": {"positions": 0, "notional": 0},
                "outside": {"positions": 0, "notional": 0}}
    mark = sorted(marks)[len(marks) // 2]
    lo, hi = mark * (1 - span_pct / 100), mark * (1 + span_pct / 100)
    width = (hi - lo) / bins
    out = [{"lo": lo + i * width, "hi": lo + (i + 1) * width, "long_ntl": 0.0, "short_ntl": 0.0, "long_traders": 0,
            "short_traders": 0, "cross_ntl": 0.0} for i in range(bins)]
    without = {"positions": 0, "notional": 0.0}
    outside = {"positions": 0, "notional": 0.0}
    for r in rows:
        ntl = float(r.notional_usd)
        if r.liq_px is None or float(r.liq_px) <= 0:
            without["positions"] += 1
            without["notional"] += ntl
            continue
        px = float(r.liq_px)
        if not lo <= px < hi:
            outside["positions"] += 1
            outside["notional"] += ntl
            continue
        b = out[min(int((px - lo) / width), bins - 1)]
        side = "long" if r.szi > 0 else "short"
        b[f"{side}_ntl"] += ntl
        b[f"{side}_traders"] += 1
        if r.margin_mode == "cross":
            b["cross_ntl"] += ntl
    return {"coin": coin, "as_of": now, "mark_px": mark, "span_pct": span_pct, "bins": out, "without_liq": without,
            "outside": outside, "positions": len(rows)}


@router.get("/structure")
def positioning_structure(session: Session = Depends(get_session), coin: str = Query("BTC", max_length=48),
                          venue: Venue = "all", cohort: Cohort = "all", min_lev: int = Query(0, ge=0, le=200),
                          entry_span: float = Query(40, gt=1, le=90), liq_span: float = Query(30, gt=1, le=90),
                          bins: int = Query(60, ge=10, le=200)):
    """Entry and liquidation structure of one coin: zones, cost basis, profit vs underwater, nearest liquidations
    and the liquidation ladder. The leverage filter applies to the liquidation side only."""
    now = datetime.now(UTC)
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort)}
        SELECT wallet_id, szi, notional_usd, entry_px, liq_px, leverage, margin_mode, mark_px, observed_at
        FROM fresh WHERE coin = :coin ORDER BY observed_at DESC"""), {"now": now, "coin": coin}).all()
    ref_row = next((r for r in rows if r.mark_px), None)
    if ref_row is None:
        return {"coin": coin, "as_of": now, "ref": None, "positions": 0}
    ref = float(ref_row.mark_px)
    data = structure.build([structure.Row(r.wallet_id, 1 if r.szi > 0 else -1, float(r.notional_usd),
                                          float(r.entry_px) if r.entry_px else None,
                                          float(r.liq_px) if r.liq_px else None, r.leverage, r.margin_mode)
                            for r in rows], ref, entry_span / 100, liq_span / 100, bins, min_lev)
    ids = [s["nearest"]["wallet_id"] for s in data["liquidations"].values()
           if isinstance(s, dict) and s.get("nearest")]
    names = {r.id: (r.address, r.display_name) for r in session.execute(text(
        "SELECT id, address, display_name FROM wallet WHERE id = ANY(:ids)"), {"ids": ids}).all()}
    for side in ("long", "short"):
        near = data["liquidations"][side]["nearest"]
        if near:
            near["address"], near["display_name"] = names.get(near.pop("wallet_id"), (None, None))
    return {"coin": coin, "as_of": now, "positions": len(rows),
            "ref": {"px": ref, "observed_at": ref_row.observed_at}, **data}


@router.get("/divergence")
def smart_vs_crowd(session: Session = Depends(get_session), venue: Venue = "all",
                   coin: str = Query("BTC", max_length=48), days: int = Query(60, ge=7, le=365)):
    """Timing-significant traders vs the whole observed sample, per coin, now and as a daily history."""
    return {"as_of": datetime.now(UTC), "venue": venue, "min_smart": divergence.MIN_SMART,
            "coins": divergence.current(session, venue)[:30], "coin": coin,
            "history": divergence.history(session, venue, coin, days)}


POSITION_COLS = """w.address, w.display_name, f.venue, f.coin, f.szi, f.notional_usd, f.mark_px, f.entry_px, f.leverage,
               f.margin_mode, f.liq_px, f.upl, f.opened_at, f.opened_at_source, f.observed_at"""


@router.get("/at-risk")
def at_risk(session: Session = Depends(get_session), venue: Venue = "all", cohort: Cohort = "all",
            coin: str | None = Query(None, max_length=48), side: Literal["long", "short", "all"] = "all",
            max_dist: float = Query(0.1, gt=0, le=1), min_notional: float = Query(0, ge=0),
            limit: int = Query(200, ge=1, le=1000)):
    """Positions closest to their venue-reported liquidation price, across coins (liquidation early warning)."""
    now = datetime.now(UTC)
    cond = (" AND f.coin = :coin" if coin else "") + {"long": " AND f.szi > 0", "short": " AND f.szi < 0", "all": ""}[side]
    base = f"""WITH {stats.fresh_cte(venue, cohort)}
        SELECT {POSITION_COLS}, {POSITION_EXTRAS}
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id
        WHERE f.liq_px > 0 AND f.mark_px > 0 AND f.notional_usd >= :mn
          AND ((f.szi > 0 AND f.liq_px < f.mark_px) OR (f.szi < 0 AND f.liq_px > f.mark_px))
          AND abs(f.mark_px - f.liq_px) / f.mark_px <= :d{cond}"""
    params = {"now": now, "mn": min_notional, "d": max_dist, "coin": coin}
    rows = session.execute(text(base + " ORDER BY liq_dist ASC LIMIT :n"), {**params, "n": limit}).mappings().all()
    buckets = session.execute(text(f"""
        SELECT CASE WHEN liq_dist <= 0.02 THEN '2' WHEN liq_dist <= 0.05 THEN '5' ELSE '10' END AS b,
               sign(szi) AS side, count(*) AS n, sum(notional_usd) AS ntl FROM ({base}) x GROUP BY 1, 2"""),
        params).mappings().all()
    return {"as_of": now, "max_dist": max_dist,
            "buckets": [{"within": r["b"], "side": "long" if r["side"] > 0 else "short", "positions": r["n"],
                         "notional": num(r["ntl"])} for r in buckets],
            "rows": [_clean(dict(r)) for r in rows]}


@router.get("/liquidated")
def suspected_liquidations(session: Session = Depends(get_session), hours: int = Query(24, ge=1, le=168),
                           venue: Venue = "all", cohort: Cohort = "all", coin: str | None = Query(None, max_length=48),
                           min_notional: float = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """Positions that disappeared at (within 0.5%) or beyond the liquidation price they last reported: inferred, not
    the exchange's liquidation record. Only closes observed after liquidation prices started to be kept on closes."""
    now = datetime.now(UTC)
    cond = stats.scope(venue, cohort, col="e.wallet_id", venue_col="e.venue")
    rows = session.execute(text(f"""
        SELECT w.address, w.display_name, e.venue, e.coin, e.side, e.szi_before, e.notional_change, e.mark_px,
               e.entry_px, e.leverage, e.liq_px, e.event_time, e.window_start, e.observed_at,
               e.notional_change / nullif(e.leverage, 0) AS margin
        FROM market_event e JOIN wallet w ON w.id = e.wallet_id
        WHERE e.kind = 'close' AND e.event_time > :since AND e.liq_px > 0 AND e.mark_px > 0
          AND e.notional_change >= :mn AND {cond} {"AND e.coin = :coin" if coin else ""}
          AND ((e.side = 'long' AND e.mark_px <= e.liq_px * (1 + :tol))
               OR (e.side = 'short' AND e.mark_px >= e.liq_px * (1 - :tol)))
        ORDER BY e.notional_change DESC LIMIT :n"""),
        {"since": now - timedelta(hours=hours), "mn": min_notional, "coin": coin, "tol": LIQ_TOLERANCE,
         "n": limit}).mappings().all()
    out = [_clean(dict(r)) for r in rows]
    totals: dict[str, dict] = {}
    for r in out:
        t = totals.setdefault(r["side"], {"side": r["side"], "positions": 0, "notional": 0.0})
        t["positions"] += 1
        t["notional"] += r["notional_change"] or 0
    return {"as_of": now, "hours": hours, "tolerance": LIQ_TOLERANCE, "totals": list(totals.values()), "rows": out}


@router.get("/underwater")
def underwater(session: Session = Depends(get_session), venue: Venue = "all", cohort: Cohort = "all",
               coin: str | None = Query(None, max_length=48), adding_only: bool = False,
               min_notional: float = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """Largest open losses, and whether the trader kept adding while underwater in the last 72 hours."""
    now = datetime.now(UTC)
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort)},
        adds AS (
          SELECT e.wallet_id, e.coin, count(*) AS n, sum(e.notional_change) AS ntl FROM market_event e
          WHERE e.kind = 'increase' AND e.event_time > :since AND e.mark_px > 0 AND e.entry_px > 0
            AND ((e.side = 'long' AND e.mark_px < e.entry_px) OR (e.side = 'short' AND e.mark_px > e.entry_px))
          GROUP BY 1, 2)
        SELECT {POSITION_COLS}, {POSITION_EXTRAS}, coalesce(a.n, 0) AS adds_underwater, a.ntl AS added_notional
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id
        LEFT JOIN adds a ON a.wallet_id = f.wallet_id AND a.coin = f.coin
        WHERE f.upl < 0 AND f.notional_usd >= :mn {"AND f.coin = :coin" if coin else ""}
          {"AND a.n > 0" if adding_only else ""}
        ORDER BY f.upl ASC LIMIT :n"""),
        {"now": now, "since": now - timedelta(hours=72), "mn": min_notional, "coin": coin, "n": limit}).mappings().all()
    return {"as_of": now, "rows": [{**_clean(dict(r)), "added_notional": num(r["added_notional"])} for r in rows]}


@router.get("/whales")
def whales(session: Session = Depends(get_session), venue: Venue = "all", cohort: Cohort = "all",
           coin: str | None = Query(None, max_length=48), limit: int = Query(30, ge=1, le=200),
           hours: int = Query(24, ge=1, le=168)):
    """The largest open positions and how each changed over the last `hours` (opens and adds positive, trims and
    closes negative), plus the largest positions closed in that time."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    flow = """sum(CASE WHEN e.kind IN ('open', 'increase', 'first_seen') THEN e.notional_change
                       WHEN e.kind IN ('decrease', 'close') THEN -e.notional_change ELSE 0 END)"""
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort)}
        SELECT {POSITION_COLS}, {POSITION_EXTRAS},
               (SELECT {flow} FROM market_event e WHERE e.wallet_id = f.wallet_id AND e.coin = f.coin
                  AND e.event_time > :since AND e.kind <> 'first_seen') AS change,
               (SELECT e.kind FROM market_event e WHERE e.wallet_id = f.wallet_id AND e.coin = f.coin
                  AND e.event_time > :since ORDER BY e.event_time DESC LIMIT 1) AS last_kind
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id
        WHERE true {"AND f.coin = :coin" if coin else ""}
        ORDER BY f.notional_usd DESC LIMIT :n"""), {"now": now, "since": since, "coin": coin, "n": limit}).mappings().all()
    cond = stats.scope(venue, cohort, col="e.wallet_id", venue_col="e.venue")
    closed = session.execute(text(f"""
        SELECT w.address, w.display_name, e.venue, e.coin, e.side, e.notional_change, e.mark_px, e.entry_px,
               e.event_time
        FROM market_event e JOIN wallet w ON w.id = e.wallet_id
        WHERE e.kind = 'close' AND e.event_time > :since AND {cond} {"AND e.coin = :coin" if coin else ""}
        ORDER BY e.notional_change DESC LIMIT 10"""), {"since": since, "coin": coin}).mappings().all()
    return {"as_of": now, "hours": hours,
            "rows": [{**_clean(dict(r)), "change": num(r["change"])} for r in rows],
            "closed": [_clean(dict(r)) for r in closed]}
