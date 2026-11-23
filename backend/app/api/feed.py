"""Read-only feed for the newsroom (newsroom/docs/03-feed-contract-v1.md): a versioned contract over existing tables.

Additive only: no new tables, no exchange calls. Numbers are decimal strings so downstream fact packets stay exact;
traders are identified by their trader key (address or okx:<id>), never by internal ids that rebuilds can change.
Disabled (404) unless ALPHASCOPE_FEED_TOKEN is set.
"""

import hmac
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.market import LIQ_TOLERANCE
from app.config import Settings, get_settings
from app.db import get_session
from app.market import stats
from app.market.observe import INTERVAL, STALE_FACTOR
from app.traders import parse_trader_key

SCHEMA = "feed.v1"
Venue = Literal["all", "hyperliquid", "okx"]
# radar health: share of watched traders polled within 2x their interval, and age of the newest poll
HEALTH_RULES = {"ok_fresh_ratio": 0.9, "lagging_fresh_ratio": 0.5, "max_latest_age_minutes": 30}


def require_feed(authorization: str | None = Header(None), settings: Settings = Depends(get_settings)) -> None:
    if not settings.feed_token:
        raise HTTPException(404)  # disabled: the feed does not exist
    if not authorization or not hmac.compare_digest(authorization.encode(), f"Bearer {settings.feed_token}".encode()):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})


def feed_session(session: Session = Depends(get_session)) -> Iterator[Session]:
    # first statements of the transaction: the feed can never write, and a slow query cannot hold the API
    session.execute(text("SET TRANSACTION READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout = '5s'"))
    yield session
    session.rollback()


router = APIRouter(prefix="/api/v1/feed", tags=["feed"], dependencies=[Depends(require_feed)])


def plain(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [plain(x) for x in v]
    return v


def rows(s: Session, sql: str, params: dict) -> list[dict]:
    return [plain(dict(r)) for r in s.execute(text(sql), params).mappings().all()]


@router.get("/meta")
def meta(settings: Settings = Depends(get_settings)):
    return {"schema": SCHEMA, "server_time": datetime.now(UTC), "settle_seconds": settings.feed_settle_seconds,
            "limits": {"events_limit": 1000, "events_scan": 5000},
            "rules": {"liq_tolerance": format(Decimal(str(LIQ_TOLERANCE)), "f"), "stale_factor": STALE_FACTOR,
                      "poll_minutes": {f"{v}:{t}": int(d.total_seconds() // 60) for (v, t), d in INTERVAL.items()},
                      "health": HEALTH_RULES}}


EVENT_SQL = """
    SELECT e.id AS source_id, w.address AS trader_key, e.venue, w.display_name, w.handle, e.coin, e.kind, e.side,
           e.szi_before, e.szi_after, e.notional_change, e.mark_px, e.entry_px, e.leverage, e.liq_px,
           e.event_time, e.time_precision, e.window_start, e.observed_at
    FROM market_event e JOIN wallet w ON w.id = e.wallet_id
    WHERE e.id > :after ORDER BY e.id LIMIT :scan"""


@router.get("/events")
def events(after_id: int = Query(..., ge=0), limit: int = Query(500, ge=1, le=1000),
           scan: int = Query(2000, ge=100, le=5000), include_first_seen: bool = False,
           min_notional: float = Query(0, ge=0), venue: Venue = "all",
           s: Session = Depends(feed_session), settings: Settings = Depends(get_settings)):
    """Radar position changes in id order. Observations commit concurrently, so an id can become visible after a
    higher one: the scan stops at the first row observed within the settle window and never skips past it. Filters
    move the cursor; the row limit does not skip unreturned matches."""
    now = datetime.now(UTC)
    settled = now - timedelta(seconds=settings.feed_settle_seconds)
    scanned = s.execute(text(EVENT_SQL), {"after": after_id, "scan": scan}).mappings().all()
    cursor, out, cut, full = after_id, [], False, False
    mn = Decimal(str(min_notional))
    for r in scanned:
        if r["observed_at"] >= settled:
            cut = True
            break
        match = ((include_first_seen or r["kind"] != "first_seen") and (venue == "all" or r["venue"] == venue)
                 and (r["notional_change"] or 0) >= mn)
        if match:
            if len(out) >= limit:
                full = True
                break
            out.append(plain(dict(r)))
        cursor = r["source_id"]
    head = s.execute(text("SELECT max(id) FROM market_event")).scalar()
    return {"schema": SCHEMA, "as_of": now, "settled_before": settled, "after_id": after_id,
            "next_after_id": cursor, "head_id": head, "has_more": full or (len(scanned) == scan and not cut),
            "rows": out}


def _wallet(s: Session, trader_key: str) -> dict:
    key = parse_trader_key(trader_key)
    if not key:
        raise HTTPException(422, "invalid trader key")
    w = s.execute(text("""
        SELECT id, address AS trader_key, venue, display_name, handle, first_seen_at, account_value, tier,
               quality_flags, enrichment_flags, day_pnl, day_roi, week_pnl, week_roi, month_pnl, month_roi,
               alltime_pnl, alltime_roi, leaderboard_fetched_at, present_in_latest
        FROM wallet WHERE address = :a"""), {"a": key}).mappings().first()
    if w is None:
        raise HTTPException(404, "trader not in database")
    return dict(w)


@router.get("/traders/{trader_key}/context")
def trader_context(trader_key: str, coin: str | None = Query(None, max_length=48),
                   cycles: int = Query(50, ge=0, le=200), okx_closes: int = Query(50, ge=0, le=200),
                   event_days: int = Query(14, ge=1, le=30), liq_days: int = Query(30, ge=1, le=60),
                   s: Session = Depends(feed_session)):
    """Everything known about one trader at this moment, with the coverage needed to word it honestly."""
    now = datetime.now(UTC)
    w = _wallet(s, trader_key)
    wid = w.pop("id")
    p = {"w": wid, "now": now, "coin": coin}
    coin_cond = " AND coin = :coin" if coin else ""
    watch = s.execute(text("""
        SELECT tier, last_observed_at, stale_after, stale_after > :now AS is_fresh, failures, last_error,
               account_value, n_positions, gross_notional
        FROM position_watch WHERE wallet_id = :w"""), p).mappings().first()
    positions = rows(s, """
        SELECT coin, szi, notional_usd, mark_px, entry_px, leverage, margin_mode, liq_px, upl, opened_at,
               opened_at_source, opened_after, first_seen_at, observed_at,
               notional_usd / nullif(leverage, 0) AS implied_margin
        FROM trader_position WHERE wallet_id = :w ORDER BY notional_usd DESC""", p)
    recent = rows(s, f"""
        SELECT id AS source_id, coin, kind, side, szi_before, szi_after, notional_change, mark_px, entry_px, leverage,
               liq_px, event_time, time_precision, window_start, observed_at
        FROM market_event WHERE wallet_id = :w AND observed_at > :since{coin_cond}
        ORDER BY observed_at, id""", {**p, "since": now - timedelta(days=event_days)})
    metrics = rows(s, """
        SELECT metric_window AS window, metric_version, computed_at, covered, span_start, span_end, n_points,
               n_valid_periods, twr, mdd, pnl, reliability, notes
        FROM wallet_metric WHERE wallet_id = :w ORDER BY metric_window""", p)
    quality = {
        "styles": rows(s, """SELECT style, version, status, confidence, n, oos, computed_at FROM style_tag
                             WHERE wallet_id = :w ORDER BY style""", p),
        "timing": rows(s, """SELECT scale, time_window AS window, version, status, n_scored, edge_shrunk, p_value,
                                    q_value, as_of FROM timing_score
                             WHERE wallet_id = :w AND coin_group = 'all' ORDER BY scale, time_window""", p),
        "copy": next(iter(rows(s, "SELECT status, grade, score, computed_at FROM copy_score WHERE wallet_id = :w",
                               p)), None),
        "health": next(iter(rows(s, """SELECT status, prev_status, changed_at, computed_at FROM trader_health
                                       WHERE wallet_id = :w""", p)), None),
    }
    coverage = s.execute(text("""
        SELECT 'fills' AS source, status, earliest_fill_at AS earliest_at, latest_fill_at AS latest_at,
               n_fills AS n_records, truncated, last_synced_at, reconstructed_at, recon_breaks
        FROM fill_sync WHERE wallet_id = :w"""), p).mappings().first()
    if coverage is None and w["venue"] == "okx":  # same mapping as the wallet positions page
        coverage = s.execute(text("""
            SELECT 'okx_positions' AS source,
                   CASE WHEN history_status IN ('target_complete', 'source_complete') THEN 'synced'
                        ELSE history_status END AS status,
                   oldest_at AS earliest_at, newest_at AS latest_at, n_positions AS n_records,
                   history_status = 'target_complete' AS truncated, history_synced_at AS last_synced_at,
                   derived_at AS reconstructed_at, 0 AS recon_breaks
            FROM okx_trader WHERE wallet_id = :w"""), p).mappings().first()
    cyc = rows(s, f"""
        SELECT coin, direction, opened_at, version, closed_at, status, complete, scale, holding_hours, entry_px,
               exit_px, max_size, max_notional, realized_pnl, fees
        FROM position_cycle WHERE wallet_id = :w{coin_cond}
        ORDER BY coalesce(closed_at, opened_at) DESC LIMIT :n""", {**p, "n": cycles}) if cycles else []
    liqs = rows(s, f"""
        SELECT time, coin, px, sz, dir, closed_pnl, liquidation FROM fill
        WHERE wallet_id = :w AND time > :since AND liquidation IS NOT NULL
          AND lower(liquidation->>'liquidatedUser') = :addr{coin_cond}
        ORDER BY time""", {**p, "since": now - timedelta(days=liq_days), "addr": w["trader_key"]}) \
        if w["venue"] == "hyperliquid" else []
    okx = None
    if w["venue"] == "okx":
        okx = {
            "closes": rows(s, f"""
                SELECT pos_id, coin, direction, open_time, close_time, max_notional, pnl, realized_pnl, fee, funding,
                       close_type, liquidated, lever
                FROM okx_position WHERE wallet_id = :w AND close_type <> 'partClose'{coin_cond}
                ORDER BY close_time DESC LIMIT :n""", {**p, "n": okx_closes}),
            "totals": plain(dict(s.execute(text("""
                SELECT count(*) AS positions, sum(realized_pnl) AS realized, count(*) FILTER (WHERE liquidated)
                       AS liquidations, count(*) FILTER (WHERE realized_pnl > 0) AS wins, min(open_time) AS since
                FROM okx_position WHERE wallet_id = :w AND close_type <> 'partClose'"""), p).mappings().one())),
            "trader": next(iter(rows(s, """SELECT history_status, pool_status, asset, asset_at, history_synced_at
                                           FROM okx_trader WHERE wallet_id = :w""", p)), None),
        }
    return {"schema": SCHEMA, "as_of": now, "trader": plain(w), "watch": plain(dict(watch)) if watch else None,
            "positions": positions, "recent_events": recent, "metrics": metrics, "quality": quality,
            "fill_coverage": plain(dict(coverage)) if coverage else None, "cycles": cyc,
            "confirmed_liquidations": liqs, "okx": okx}


@router.get("/positions/at-risk")
def positions_at_risk(max_dist: float = Query(0.05, gt=0, le=0.2), min_notional: float = Query(250_000, ge=0),
                      venue: Venue = "all", limit: int = Query(200, ge=1, le=500),
                      s: Session = Depends(feed_session)):
    """Fresh positions within max_dist of their venue-reported liquidation price (none are estimated)."""
    now = datetime.now(UTC)
    out = rows(s, f"""
        WITH {stats.fresh_cte(venue, "all")}
        SELECT w.address AS trader_key, f.venue, w.display_name, f.coin, f.szi, f.notional_usd, f.mark_px, f.entry_px,
               f.leverage, f.margin_mode, f.liq_px, f.upl, f.opened_at, f.opened_at_source, f.first_seen_at,
               f.observed_at, pw.account_value, abs(f.mark_px - f.liq_px) / f.mark_px AS liq_dist
        FROM fresh f JOIN wallet w ON w.id = f.wallet_id JOIN position_watch pw ON pw.wallet_id = f.wallet_id
        WHERE f.liq_px > 0 AND f.mark_px > 0 AND f.notional_usd >= :mn
          AND ((f.szi > 0 AND f.liq_px < f.mark_px) OR (f.szi < 0 AND f.liq_px > f.mark_px))
          AND abs(f.mark_px - f.liq_px) / f.mark_px <= :d
        ORDER BY liq_dist ASC LIMIT :n""", {"now": now, "mn": min_notional, "d": max_dist, "n": limit})
    return {"schema": SCHEMA, "as_of": now, "max_dist": max_dist, "rows": out}


def radar_status(cov: dict, now: datetime) -> str:
    if not cov["watched"]:
        return "empty"
    ratio = cov["fresh"] / cov["watched"]
    recent = cov["latest"] is not None and now - cov["latest"] <= timedelta(minutes=HEALTH_RULES["max_latest_age_minutes"])
    if ratio >= HEALTH_RULES["ok_fresh_ratio"] and recent:
        return "ok"
    return "lagging" if ratio >= HEALTH_RULES["lagging_fresh_ratio"] and recent else "stale"


@router.get("/health")
def health(s: Session = Depends(feed_session)):
    """Freshness per source. Radar sweeps do not write ingest_run, so their health comes from position_watch."""
    now = datetime.now(UTC)
    sources: dict = {}
    for venue in ("hyperliquid", "okx"):
        cov = stats.coverage(s, venue, "all", now)
        sources[f"radar_{venue}"] = {**plain(cov), "fresh_ratio": round(cov["fresh"] / cov["watched"], 4)
                                     if cov["watched"] else None, "status": radar_status(cov, now)}
    head = s.execute(text("SELECT id, observed_at FROM market_event ORDER BY id DESC LIMIT 1")).first()
    sources["market_event"] = {"head_id": head[0] if head else None, "head_observed_at": head[1] if head else None}
    sources["jobs"] = rows(s, """
        SELECT DISTINCT ON (kind) kind, status, started_at, finished_at FROM ingest_run
        WHERE status IN ('success', 'not_modified', 'partial') ORDER BY kind, started_at DESC""", {})
    sources["fill_archive"] = plain(dict(s.execute(text("""
        SELECT max(last_synced_at) AS latest_synced_at, count(*) FILTER (WHERE status = 'synced') AS synced
        FROM fill_sync""")).mappings().one()))
    sources["okx_history"] = plain(dict(s.execute(text("""
        SELECT max(history_synced_at) AS latest_synced_at,
               count(*) FILTER (WHERE history_synced_at > :d) AS synced_24h FROM okx_trader"""),
        {"d": now - timedelta(hours=24)}).mappings().one()))
    sources["copy_score"] = {"computed_at": s.execute(text("SELECT max(computed_at) FROM copy_score")).scalar()}
    return {"schema": SCHEMA, "as_of": now, "rules": HEALTH_RULES, "sources": sources}
