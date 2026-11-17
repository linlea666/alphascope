"""Market positioning radar over the observed sample of traders (docs/12). Every number can be drilled into the
contributing traders and positions; nothing here describes the whole exchange."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import num
from app.db import get_session
from app.market import stats
from app.market.stats import Cohort, Venue

router = APIRouter(prefix="/api/v1/market", tags=["market"])
NUMERIC = ("long_ntl", "short_ntl", "lev10_long_ntl", "lev10_short_ntl", "lev20_long_ntl", "lev20_short_ntl",
           "liq_known_ntl", "mark_px", "median_leverage", "szi", "notional_usd", "entry_px", "liq_px", "upl",
           "account_value", "notional_change", "szi_before", "szi_after", "gross_notional")


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
                 min_lev: int = Query(0, ge=0, le=200), limit: int = Query(200, ge=1, le=1000)):
    """The traders and positions behind a coin's numbers (same freshness and cohort rules as the aggregates)."""
    now = datetime.now(UTC)
    side_sql = {"long": "AND f.szi > 0", "short": "AND f.szi < 0", "all": ""}[side]
    rows = session.execute(text(f"""
        WITH {stats.fresh_cte(venue, cohort, min_lev)}
        SELECT w.address, w.display_name, f.venue, f.szi, f.notional_usd, f.mark_px, f.entry_px, f.leverage,
               f.margin_mode, f.liq_px, f.upl, f.opened_at, f.opened_at_source, f.opened_after, f.first_seen_at,
               f.observed_at, pw.account_value
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id JOIN position_watch pw ON pw.wallet_id = f.wallet_id
        WHERE f.coin = :coin {side_sql}
        ORDER BY f.notional_usd DESC LIMIT :n"""), {"now": now, "coin": coin, "n": limit}).mappings().all()
    return {"as_of": now, "coin": coin, "rows": [_clean(dict(r)) for r in rows]}


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
            cohort: Cohort = "all", days: int = Query(7, ge=1, le=90)):
    rows = session.execute(text("""
        SELECT bucket_ts, observed, long_traders, short_traders, long_ntl, short_ntl, lev20_long_ntl,
               lev20_short_ntl, opens, closes
        FROM market_snapshot WHERE coin = :coin AND venue = :v AND cohort = :c AND bucket_ts > :since
        ORDER BY bucket_ts"""), {"coin": coin, "v": venue, "c": cohort,
                                 "since": datetime.now(UTC) - timedelta(days=days)}).mappings().all()
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
