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

from app.api.common import venue_clause
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
    # the venue's all-time P&L curve (portfolio pnlHistory), thinned to at most 90 points for a card
    curve = s.execute(text("""SELECT ts, pnl, account_value FROM equity_point WHERE wallet_id = :w AND series = 'allTime'
                              ORDER BY ts"""), p).all()
    step = max(1, -(-len(curve) // 90))
    pnl_series = [{"t": r[0], "pnl": format(r[1], "f"), "account_value": format(r[2], "f")}
                  for r in curve[::step] + ([curve[-1]] if curve and (len(curve) - 1) % step else [])]
    return {"schema": SCHEMA, "as_of": now, "trader": plain(w), "watch": plain(dict(watch)) if watch else None,
            "positions": positions, "recent_events": recent, "metrics": metrics, "quality": quality,
            "fill_coverage": plain(dict(coverage)) if coverage else None, "cycles": cyc,
            "confirmed_liquidations": liqs, "okx": okx, "pnl_series": pnl_series}


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


# ---- research columns (newsroom docs/11): read-only views over tables AlphaScope already computes ----

COPY_PARAMS = "default-v2"  # app/factors/copy_run.PARAMS_KEY


@router.get("/copy/board")
def copy_vs_board(window: Literal["7d", "30d", "90d"] = "30d", limit: int = Query(20, ge=1, le=100),
                  s: Session = Depends(feed_session)):
    """The latest complete OKX board for a window next to the copy backtest of the same traders and window: the
    official ROI against what following the trades would have returned (assumptions in `assumptions`)."""
    now = datetime.now(UTC)
    days = int(window[:-1])
    run = s.execute(text("""SELECT id, business_date, completed_at FROM okx_leaderboard_run
                            WHERE time_window = :w AND status = 'complete' ORDER BY business_date DESC, id DESC LIMIT 1"""),
                    {"w": window}).mappings().first()
    out = rows(s, """
        SELECT r.rank, w.address AS trader_key, w.display_name, r.pnl, r.pnl_ratio, r.win_rate, r.max_drawdown, r.asset,
               b.ret AS copy_ret, b.mdd AS copy_mdd, b.leader_ret, b.retention, b.trades, b.liquidations,
               b.approx_equity, b.computed_at AS backtest_at
        FROM okx_leaderboard_row r JOIN wallet w ON w.id = r.wallet_id
        LEFT JOIN copy_backtest b ON b.wallet_id = r.wallet_id AND b.window_days = :d AND b.params_key = :p
        WHERE r.run_id = :run ORDER BY r.rank LIMIT :n""",
        {"d": days, "p": COPY_PARAMS, "run": run["id"], "n": limit}) if run else []
    return {"schema": SCHEMA, "as_of": now, "window": window, "run": plain(dict(run)) if run else None,
            "assumptions": {"params_key": COPY_PARAMS, "ratio": "100%", "delay_seconds": 30, "fee_bp": 4.5,
                            "slippage_bp": 3, "leverage_cap": 10, "okx_equity": "current lead assets"},
            "rows": out}


@router.get("/okx/dropouts")
def okx_dropouts(days: int = Query(30, ge=1, le=180), max_best_rank: int = Query(50, ge=1, le=500),
                 limit: int = Query(30, ge=1, le=100), s: Session = Depends(feed_session)):
    """Traders who were on the 7d board and are no longer, with what their position history shows since."""
    now = datetime.now(UTC)
    out = rows(s, """
        SELECT w.address AS trader_key, w.display_name, p.first_seen_at, p.last_seen_at, p.seen_days, p.streak,
               p.best_rank, p.last_rank, t.history_status,
               (SELECT count(*) FROM okx_position o WHERE o.wallet_id = p.wallet_id AND o.close_type <> 'partClose'
                  AND o.close_time > p.last_seen_at) AS closes_since,
               (SELECT sum(o.realized_pnl) FROM okx_position o WHERE o.wallet_id = p.wallet_id
                  AND o.close_type <> 'partClose' AND o.close_time > p.last_seen_at) AS realized_since,
               (SELECT count(*) FROM okx_position o WHERE o.wallet_id = p.wallet_id AND o.liquidated
                  AND o.close_time > p.last_seen_at) AS liquidations_since,
               (SELECT max(o.close_time) FROM okx_position o WHERE o.wallet_id = p.wallet_id) AS last_close
        FROM okx_presence p JOIN wallet w ON w.id = p.wallet_id LEFT JOIN okx_trader t ON t.wallet_id = p.wallet_id
        WHERE p.time_window = '7d' AND NOT p.currently_seen AND p.last_seen_at > :since AND p.best_rank <= :r
        ORDER BY p.best_rank, p.last_seen_at DESC LIMIT :n""",
        {"since": now - timedelta(days=days), "r": max_best_rank, "n": limit})
    return {"schema": SCHEMA, "as_of": now, "days": days, "rows": out}


def _thin(series: list | None, keep: int = 90) -> list:
    """A board's daily cumulative-return series, last `keep` points, as [[time, value], ...]."""
    return [[a, b] for a, b in (series or [])[-keep:]]


def _peak_drawdown(series: list | None) -> float | None:
    """How far the equity curve sits below its own peak now (0.4 = 40% off the high), from cumulative returns."""
    vals = [1 + float(v) for _, v in (series or []) if v is not None]
    if not vals:
        return None
    peak = max(vals)
    return round(1 - vals[-1] / peak, 4) if peak > 0 else None


def _latest_runs(s: Session, window: str) -> tuple[dict | None, dict | None]:
    runs = s.execute(text("""SELECT id, business_date, completed_at FROM okx_leaderboard_run
                             WHERE time_window = :w AND status = 'complete' ORDER BY business_date DESC, id DESC LIMIT 8"""),
                     {"w": window}).mappings().all()
    latest = runs[0] if runs else None
    prev = next((r for r in runs[1:] if r["business_date"] < latest["business_date"]), None) if latest else None
    return (dict(latest) if latest else None), (dict(prev) if prev else None)


@router.get("/okx/board/movers")
def okx_board_movers(window: Literal["7d", "30d", "90d"] = "7d", limit: int = Query(30, ge=1, le=100),
                     s: Session = Depends(feed_session)):
    """The latest complete OKX board for a window as people, not a table: each trader's return, money made, how many
    times the account multiplied, how far it is off its own peak, and the move since the previous day's board.
    `dropped` lists who was in the previous board's top `limit` and is gone now."""
    now = datetime.now(UTC)
    latest, prev = _latest_runs(s, window)
    out, dropped = [], []
    if latest:
        for r in rows(s, """
                SELECT r.rank, w.address AS trader_key, w.display_name, w.handle, r.pnl, r.pnl_ratio, r.win_rate,
                       r.max_drawdown, r.asset, r.rates, p.rank AS prev_rank
                FROM okx_leaderboard_row r JOIN wallet w ON w.id = r.wallet_id
                LEFT JOIN okx_leaderboard_row p ON p.wallet_id = r.wallet_id AND p.run_id = :prev
                WHERE r.run_id = :run ORDER BY r.rank LIMIT :n""",
                {"run": latest["id"], "prev": prev["id"] if prev else -1, "n": limit}):
            series = r.pop("rates")
            ratio = r["pnl_ratio"]
            out.append({**r, "rank_change": (r["prev_rank"] - r["rank"]) if r["prev_rank"] else None,
                        "new_entry": prev is not None and r["prev_rank"] is None,
                        "multiple": str(round(1 + float(ratio), 2)) if ratio is not None else None,
                        "off_peak": _peak_drawdown(series), "days": len(series or [])})
        if prev:
            dropped = rows(s, """
                SELECT p.rank AS prev_rank, w.address AS trader_key, w.display_name, p.pnl, p.pnl_ratio
                FROM okx_leaderboard_row p JOIN wallet w ON w.id = p.wallet_id
                WHERE p.run_id = :prev AND p.rank <= :n
                  AND NOT EXISTS (SELECT 1 FROM okx_leaderboard_row r WHERE r.run_id = :run AND r.wallet_id = p.wallet_id)
                ORDER BY p.rank""", {"prev": prev["id"], "run": latest["id"], "n": limit})
    return {"schema": SCHEMA, "as_of": now, "window": window, "run": plain(latest), "previous_run": plain(prev),
            "rows": plain(out), "dropped": dropped}


@router.get("/okx/traders/{trader_key}/profile")
def okx_trader_profile(trader_key: str, s: Session = Depends(feed_session)):
    """One OKX trader as a character: public nickname and profile, every board they sit on (rank, return, curve), how
    long they have been on each, and the last 30 days of closed positions in brief."""
    now = datetime.now(UTC)
    w = s.execute(text("""SELECT w.id, w.address AS trader_key, w.display_name, w.handle, t.profile, t.asset, t.asset_at,
                                 t.pool_status, t.history_status
                          FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id
                          WHERE w.address = :k AND w.venue = 'okx'"""), {"k": trader_key}).mappings().first()
    if w is None:
        raise HTTPException(404, "unknown OKX trader")
    prof = w["profile"] or {}
    boards = []
    for window in ("7d", "30d", "90d"):
        latest, _ = _latest_runs(s, window)
        row = s.execute(text("""SELECT rank, pnl, pnl_ratio, win_rate, max_drawdown, asset, rates FROM okx_leaderboard_row
                                WHERE run_id = :r AND wallet_id = :w"""),
                        {"r": latest["id"] if latest else -1, "w": w["id"]}).mappings().first()
        pres = s.execute(text("""SELECT first_seen_at, last_seen_at, seen_days, streak, currently_seen, best_rank, last_rank
                                 FROM okx_presence WHERE wallet_id = :w AND time_window = :t"""),
                         {"w": w["id"], "t": window}).mappings().first()
        boards.append({"window": window, "on_board": row is not None,
                       **({k: row[k] for k in ("rank", "pnl", "pnl_ratio", "win_rate", "max_drawdown", "asset")} if row else {}),
                       "multiple": str(round(1 + float(row["pnl_ratio"]), 2)) if row and row["pnl_ratio"] is not None else None,
                       "off_peak": _peak_drawdown(row["rates"]) if row else None,
                       "series": _thin(row["rates"]) if row else [], "presence": dict(pres) if pres else None})
    closes = s.execute(text("""
        SELECT count(*) AS closes, count(*) FILTER (WHERE realized_pnl > 0) AS wins, sum(realized_pnl) AS realized,
               count(*) FILTER (WHERE liquidated) AS liquidations, max(realized_pnl) AS best, min(realized_pnl) AS worst,
               max(lever) AS max_lever, mode() WITHIN GROUP (ORDER BY coin) AS top_coin,
               avg(CASE WHEN direction = 'short' THEN 1 ELSE 0 END) AS short_share
        FROM okx_position WHERE wallet_id = :w AND close_type <> 'partClose' AND close_time > :since"""),
        {"w": w["id"], "since": now - timedelta(days=30)}).mappings().one()
    return {"schema": SCHEMA, "as_of": now, "trader_key": w["trader_key"],
            "display_name": w["display_name"] or prof.get("nickName"), "handle": w["handle"] or prof.get("uniqueName"),
            "followers": prof.get("followerCount"), "bio": prof.get("bio"), "joined": prof.get("joinTime"),
            "asset": w["asset"], "asset_at": w["asset_at"], "pool_status": w["pool_status"],
            "boards": plain(boards), "closes_30d": plain(dict(closes))}


@router.get("/okx/traders")
def okx_trader_search(query: str = Query(..., min_length=1, max_length=64), limit: int = Query(20, ge=1, le=50),
                      s: Session = Depends(feed_session)):
    """Find OKX traders by nickname or profile name (for picking star traders)."""
    now = datetime.now(UTC)
    latest, _ = _latest_runs(s, "30d")
    out = rows(s, """
        SELECT w.address AS trader_key, w.display_name, w.handle, t.asset, t.pool_status, r.rank AS rank_30d,
               r.pnl_ratio AS pnl_ratio_30d, r.pnl AS pnl_30d
        FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id
        LEFT JOIN okx_leaderboard_row r ON r.wallet_id = w.id AND r.run_id = :run
        WHERE w.venue = 'okx' AND (w.display_name ILIKE :q OR w.handle ILIKE :q)
        ORDER BY r.rank NULLS LAST, t.asset DESC NULLS LAST LIMIT :n""",
        {"q": f"%{query}%", "run": latest["id"] if latest else -1, "n": limit})
    return {"schema": SCHEMA, "as_of": now, "rows": out}


@router.get("/market/structure")
def market_structure(coin: str = Query("BTC", max_length=48), venue: Venue = "all", s: Session = Depends(feed_session)):
    """Entry and liquidation structure of the observed sample for one coin (venue-reported liquidation prices only)."""
    from app.api.market import positioning_structure
    now = datetime.now(UTC)
    data = positioning_structure(session=s, coin=coin, venue=venue, cohort="all", min_lev=0, entry_span=40,
                                 liq_span=30, bins=60)
    for side in ("long", "short"):
        near = (data.get("liquidations") or {}).get(side, {}).get("nearest") if data.get("ref") else None
        if near:
            near["trader_key"] = near.pop("address", None)
    return {"schema": SCHEMA, "as_of": now, "venue": venue, "coverage": plain(stats.coverage(s, venue, "all", now)),
            **plain(data)}


@router.get("/research/forecasts")
def forecast_ledger(coin: str = Query("BTC", max_length=12), days: int = Query(30, ge=1, le=180),
                    s: Session = Depends(feed_session)):
    """Pre-registered forecasts (shadow mode) and how they scored against the baseline, failures included."""
    from app.research.forward import skill
    now = datetime.now(UTC)
    raw = s.execute(text("""
        SELECT model, horizon, issued_at, outcome->>'status' AS status, (outcome->>'p_brier')::float AS brier,
               (outcome->>'baseline_brier')::float AS base_brier
        FROM research_forecast WHERE coin = :c AND issued_at > :since ORDER BY model, horizon, issued_at"""),
        {"c": coin, "since": now - timedelta(days=days)}).mappings().all()
    groups: dict = {}
    for r in raw:
        groups.setdefault((r["model"], r["horizon"]), []).append(r)
    scores = []
    for (model, horizon), items in groups.items():
        done = [r for r in items if r["status"] == "settled"]
        k = skill([(r["brier"], r["base_brier"]) for r in done], horizon)
        scores.append({"model": model, "horizon": horizon, "issued": len(items), "settled": len(done),
                       "missing": sum(r["status"] == "missing" for r in items),
                       "brier": sum(r["brier"] for r in done) / len(done) if done else None,
                       "base_brier": sum(r["base_brier"] for r in done) / len(done) if done else None,
                       "beat_baseline": sum(r["brier"] < r["base_brier"] for r in done),
                       "skill_p": k["p"], "blocks": k["blocks"]})
    return {"schema": SCHEMA, "as_of": now, "coin": coin, "days": days, "status": "shadow", "scores": scores}


@router.get("/research/funding")
def funding_and_divergence(coin: str = Query("BTC", max_length=12), hours: int = Query(24, ge=1, le=168),
                           s: Session = Depends(feed_session)):
    """Latest funding reading with its rank against the prior year, and the specialist-vs-matched-control long share
    from hourly research frames (an observed sample; a screen, not a tradable signal)."""
    now = datetime.now(UTC)
    obs = s.execute(text("""SELECT bucket, payload->'readings'->'funding'->'data' AS funding FROM exchange_observation
                            WHERE coin = :c ORDER BY bucket DESC LIMIT 1"""), {"c": coin}).mappings().first()
    frames = rows(s, """
        SELECT bucket_ts, venue, (metrics->'smart'->>'n')::int AS smart_n, metrics->'smart'->>'long_share' AS smart_long,
               (metrics->'control'->>'n')::int AS control_n, metrics->'control'->>'long_share' AS control_long,
               metrics->>'divergence' AS divergence, (metrics->>'n')::int AS sample_n
        FROM research_frame WHERE coin = :c AND bucket_ts > :since ORDER BY bucket_ts DESC, venue""",
        {"c": coin, "since": now - timedelta(hours=hours)})
    f = (obs or {}).get("funding") or {}
    return {"schema": SCHEMA, "as_of": now, "coin": coin,
            "funding": {"bucket": obs["bucket"] if obs else None, "rate": f.get("rate"), "source_at": f.get("source_at"),
                        "level": (f.get("history") or {}).get("level"),
                        "percentile": (f.get("history") or {}).get("percentile")},
            "frames": frames}


@router.get("/cycles/holding")
def holding_profile(days: int = Query(90, ge=7, le=365), venue: Venue = "all", s: Session = Depends(feed_session)):
    """How long complete closed cycles were held, winners against losers (net of fees), per scale."""
    now = datetime.now(UTC)
    out = rows(s, f"""
        SELECT scale, (realized_pnl - fees > 0) AS winner, count(*) AS n,
               percentile_cont(0.25) WITHIN GROUP (ORDER BY holding_hours) AS p25,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY holding_hours) AS median,
               percentile_cont(0.75) WITHIN GROUP (ORDER BY holding_hours) AS p75,
               count(DISTINCT c.wallet_id) AS traders
        FROM position_cycle c JOIN wallet w ON w.id = c.wallet_id
        WHERE scale IN ('scalp', 'short', 'swing', 'macro') AND closed_at > :since AND complete AND status <> 'open'
              {venue_clause(venue)}
        GROUP BY 1, 2 ORDER BY 1, 2""", {"since": now - timedelta(days=days)})
    return {"schema": SCHEMA, "as_of": now, "days": days, "venue": venue, "rows": out}


@router.get("/cycles/closed")
def closed_cycles(hours: int = Query(48, ge=1, le=168), min_streak: int = Query(0, ge=0, le=50),
                  limit: int = Query(200, ge=1, le=1000), venue: Venue = "all", s: Session = Depends(feed_session)):
    """Complete cycles closed recently, with the winning streak that preceded each (complete cycles only, net of fees)
    and whether that streak's start is known (a loss before it). Cycles are keyed naturally: rebuilds change ids."""
    now = datetime.now(UTC)
    recent = s.execute(text("""
        SELECT c.wallet_id, w.address AS trader_key, w.venue, w.display_name, c.coin, c.direction, c.opened_at,
               c.version, c.closed_at, c.status, c.scale, c.holding_hours, c.entry_px, c.exit_px, c.max_notional,
               c.realized_pnl, c.fees, f.status AS coverage_status, f.truncated, f.earliest_fill_at
        FROM position_cycle c JOIN wallet w ON w.id = c.wallet_id LEFT JOIN fill_sync f ON f.wallet_id = c.wallet_id
        WHERE c.scale IN ('scalp', 'short', 'swing', 'macro') AND c.closed_at > :since AND c.complete
          AND c.status <> 'open'""" + venue_clause(venue) + """
        ORDER BY c.closed_at DESC LIMIT :n"""), {"since": now - timedelta(hours=hours), "n": limit}).mappings().all()
    out = []
    for r in recent:
        nets = s.execute(text("""SELECT realized_pnl - fees FROM position_cycle
                                 WHERE wallet_id = :w AND complete AND status <> 'open' AND closed_at < :t
                                 ORDER BY closed_at DESC LIMIT 200"""), {"w": r["wallet_id"], "t": r["closed_at"]}
                         ).scalars().all()
        streak, known = 0, False
        for net in nets:
            if net <= 0:
                known = True
                break
            streak += 1
        if streak >= min_streak:
            row = {k: v for k, v in r.items() if k != "wallet_id"}
            out.append(plain({**row, "prior_streak": streak, "prior_known": known}))
    return {"schema": SCHEMA, "as_of": now, "hours": hours, "rows": out}


@router.get("/market/coin-positions")
def coin_positions(coin: str = Query(..., max_length=48), venue: Venue = "all", top: int = Query(10, ge=1, le=50),
                   s: Session = Depends(feed_session)):
    """Live cross-section of one coin in the observed sample: per side, how many fresh traders hold it, their total
    notional and the largest positions (trader key, size, leverage), plus the same split for traders with a
    significant timing score (q < 0.1). Positions count only while their trader's poll is fresh."""
    now = datetime.now(UTC)
    timing = stats.COHORTS["timing"].format(col="f.wallet_id")
    p = {"now": now, "coin": coin, "n": top}
    sides = rows(s, f"""
        WITH {stats.fresh_cte(venue, "all")}
        SELECT CASE WHEN f.szi > 0 THEN 'long' ELSE 'short' END AS side, count(*) AS traders,
               sum(f.notional_usd) AS notional, count(*) FILTER (WHERE {timing}) AS timing_traders,
               coalesce(sum(f.notional_usd) FILTER (WHERE {timing}), 0) AS timing_notional
        FROM fresh f WHERE f.coin = :coin AND f.szi <> 0 GROUP BY 1 ORDER BY 1""", p)
    largest = rows(s, f"""
        WITH {stats.fresh_cte(venue, "all")}
        SELECT side, rank, trader_key, venue, notional_usd, leverage, entry_px, liq_px, timing_significant FROM (
            SELECT CASE WHEN f.szi > 0 THEN 'long' ELSE 'short' END AS side, w.address AS trader_key, f.venue,
                   f.notional_usd, f.leverage, f.entry_px, f.liq_px, {timing} AS timing_significant,
                   row_number() OVER (PARTITION BY f.szi > 0 ORDER BY f.notional_usd DESC) AS rank
            FROM fresh f JOIN wallet w ON w.id = f.wallet_id WHERE f.coin = :coin AND f.szi <> 0) x
        WHERE rank <= :n ORDER BY side, rank""", p)
    return {"schema": SCHEMA, "as_of": now, "coin": coin, "venue": venue,
            "coverage": plain(stats.coverage(s, venue, "all", now)), "sides": sides, "largest": largest}


@router.get("/market/groups")
def trader_groups(coin: str = Query("BTC", max_length=48), s: Session = Depends(feed_session)):
    """Long / short split of each trader group in the latest hourly snapshot, and seven days earlier: Hyperliquid
    wallets, OKX lead traders and the AlphaScope cohorts. Groups overlap (a cohort is part of its venue)."""
    now = datetime.now(UTC)
    latest = s.execute(text("SELECT max(bucket_ts) FROM market_snapshot WHERE coin = :c"), {"c": coin}).scalar()
    out = []
    if latest:
        out = rows(s, """
            SELECT venue, cohort, bucket_ts, long_traders, short_traders, long_ntl, short_ntl, observed
            FROM market_snapshot WHERE coin = :c AND bucket_ts IN (:t, :w) ORDER BY bucket_ts DESC, venue, cohort""",
            {"c": coin, "t": latest, "w": latest - timedelta(days=7)})
    return {"schema": SCHEMA, "as_of": now, "coin": coin, "latest": latest, "rows": out}
