"""Discovery helpers on top of the research tables: candles for the zone brush chart, the swing-leg hall of fame,
a trader's highlights and cautions, a follow backtest from the flow-adjusted equity index, and side-by-side
comparison. Everything is computed from archived data and returned with its sample size."""

import math
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import VenueFilter, num, venue_clause
from app.db import get_session
from app.quant.returns import Point, index_path
from app.research.behavior import fingerprint
from app.research.timing import TIMING_VERSION
from app.sim.backtest import DELAYS, BacktestParams, backtest
from app.sim.engine import MAX_LEVERAGE
from app.traders import parse_trader_key

router = APIRouter(prefix="/api/v1", tags=["explore"])

ZONE_PAD = 0.03  # price band around a leg's start / end used for "caught the turn" and the generated queries
CANDLE_SOURCES = ("hyperliquid", "binance_um")


def _wallet(session: Session, address: str) -> dict:
    key = parse_trader_key(address)
    if not key:
        raise HTTPException(422, "invalid address")
    w = session.execute(text("""
        SELECT w.id, w.address, w.display_name, w.venue, w.account_value, t.asset
        FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id WHERE w.address = :a"""), {"a": key}).mappings().first()
    if w is None:
        raise HTTPException(404, "wallet not in database")
    return dict(w)


def _price_at(session: Session, coin: str, ts: datetime) -> float | None:
    for src in CANDLE_SOURCES:
        px = session.execute(text("""
            SELECT c FROM market_candle WHERE source = :s AND coin = :coin AND interval = '1h' AND t <= :t
            ORDER BY t DESC LIMIT 1"""), {"s": src, "coin": coin, "t": ts}).scalar()
        if px is not None:
            return float(px)
    return None


@router.get("/candles")
def candles(session: Session = Depends(get_session), coin: str = Query("BTC", max_length=48),
            interval: Literal["1h", "4h", "1d"] = "1d", since: date = date(2026, 1, 1)):
    """OHLC for charts. 4h is aggregated from 1h. Hyperliquid's own candles first, Binance USDT-M otherwise."""
    base = "1h" if interval == "4h" else interval
    for src in CANDLE_SOURCES:
        rows = session.execute(text("""
            SELECT t, o, h, l, c FROM market_candle WHERE source = :s AND coin = :coin AND interval = :i AND t >= :since
            ORDER BY t LIMIT 20000"""), {"s": src, "coin": coin, "i": base, "since": since}).all()
        if rows:
            break
    else:
        return {"coin": coin, "interval": interval, "source": None, "rows": []}
    out = [[t, float(o), float(h), float(lo), float(c)] for t, o, h, lo, c in rows]
    if interval == "4h":
        grouped: dict[datetime, list] = {}
        for t, o, h, lo, c in out:
            k = t.replace(hour=t.hour - t.hour % 4)
            g = grouped.get(k)
            grouped[k] = [k, o, h, lo, c] if g is None else [k, g[1], max(g[2], h), min(g[3], lo), c]
        out = list(grouped.values())
    return {"coin": coin, "interval": interval, "source": src, "rows": out}


@router.get("/legs/hall")
def legs_hall(session: Session = Depends(get_session), coin: str = Query("BTC", max_length=48),
              legs: int = Query(6, ge=1, le=20), venue: VenueFilter = "all",
              min_notional: float = Query(10_000, ge=0)):
    """Per recent macro swing leg: who captured most of it, who entered right at the turn, who sat on the wrong side.
    The last leg may be provisional (the reversal is not confirmed yet)."""
    rows = session.execute(text("""
        SELECT leg_start, max(leg_end) AS leg_end, max(direction) AS direction, max(leg_change) AS leg_change,
               bool_and(confirmed) AS confirmed, count(*) AS wallets
        FROM leg_capture WHERE coin = :coin GROUP BY leg_start ORDER BY leg_start DESC LIMIT :n"""),
        {"coin": coin, "n": legs}).mappings().all()
    vc = venue_clause(venue)
    out = []
    for leg in rows:
        start, end, up = leg["leg_start"], leg["leg_end"], leg["direction"] == "up"
        change = float(leg["leg_change"])
        start_px = _price_at(session, coin, start)
        end_px = start_px * (1 + change) if start_px else None
        person = """w.address, w.display_name, w.venue"""
        capturers = session.execute(text(f"""
            SELECT {person}, l.capture_ratio, l.pnl_mark, l.max_notional, l.time_aligned, l.coverage
            FROM leg_capture l JOIN wallet w ON w.id = l.wallet_id
            WHERE l.coin = :coin AND l.leg_start = :ls AND l.max_notional >= :mn AND l.time_aligned >= 0.5
              AND l.capture_ratio > 0{vc}
            ORDER BY l.capture_ratio DESC, l.pnl_mark DESC LIMIT 5"""),
            {"coin": coin, "ls": start, "mn": min_notional}).mappings().all()
        wrong = session.execute(text(f"""
            SELECT {person}, l.pnl_mark, l.max_notional, l.time_against
            FROM leg_capture l JOIN wallet w ON w.id = l.wallet_id
            WHERE l.coin = :coin AND l.leg_start = :ls AND l.time_against >= 0.5 AND l.pnl_mark < 0{vc}
            ORDER BY l.pnl_mark ASC LIMIT 3"""), {"coin": coin, "ls": start}).mappings().all()
        turn = {"wallets": 0, "rows": []}
        if start_px:
            dur = end - start
            window = (start - dur * 0.1, start + dur * 0.15)
            turn_rows = session.execute(text(f"""
                SELECT {person}, min(c.opened_at) AS opened_at,
                       sum(c.entry_px * c.max_notional) / nullif(sum(c.max_notional), 0) AS entry_px,
                       sum(c.max_notional) AS notional, sum(c.realized_pnl - c.fees) AS net
                FROM position_cycle c JOIN wallet w ON w.id = c.wallet_id
                WHERE c.coin = :coin AND c.direction = :dir AND c.opened_at BETWEEN :a AND :b
                  AND c.entry_px BETWEEN :lo AND :hi{vc}
                GROUP BY w.id, w.address, w.display_name, w.venue ORDER BY sum(c.max_notional) DESC"""),
                {"coin": coin, "dir": "long" if up else "short", "a": window[0], "b": window[1],
                 "lo": start_px * (1 - ZONE_PAD), "hi": start_px * (1 + ZONE_PAD)}).mappings().all()
            turn = {"wallets": len(turn_rows), "rows": [
                {**dict(r), "entry_px": num(r["entry_px"]), "notional": num(r["notional"]), "net": num(r["net"])}
                for r in turn_rows[:3]]}
        query = None
        if start_px and end_px:
            query = {"direction": "long" if up else "short",
                     "entry_min": round(start_px * (1 - ZONE_PAD)), "entry_max": round(start_px * (1 + ZONE_PAD)),
                     "exit_min": round(min(end_px * (1 - ZONE_PAD), end_px * (1 + ZONE_PAD))),
                     "exit_max": round(max(end_px * (1 - ZONE_PAD), end_px * (1 + ZONE_PAD))),
                     "start": str((start - timedelta(days=7)).date()), "end": str((start + (end - start) * 0.5).date())}
        out.append({
            "leg_start": start, "leg_end": end, "direction": leg["direction"], "leg_change": change,
            "confirmed": leg["confirmed"], "wallets": leg["wallets"], "start_px": start_px, "end_px": end_px,
            "capturers": [{**dict(r), **{k: num(r[k]) for k in ("capture_ratio", "pnl_mark", "max_notional",
                                                              "time_aligned")}} for r in capturers],
            "turn": turn,
            "wrong": [{**dict(r), **{k: num(r[k]) for k in ("pnl_mark", "max_notional", "time_against")}}
                      for r in wrong],
            "query": query,
        })
    return {"coin": coin, "zone_pad": ZONE_PAD, "min_notional": min_notional, "legs": out}


def _cycle(r) -> dict:
    d = {k: (num(v) if hasattr(v, "is_finite") else v) for k, v in dict(r).items()}
    if d.get("entry_px") and d.get("exit_px"):
        sign = 1 if d["direction"] == "long" else -1
        d["move"] = sign * (d["exit_px"] - d["entry_px"]) / d["entry_px"]
    return d


@router.get("/wallets/{address}/highlights")
def wallet_highlights(address: str, session: Session = Depends(get_session)):
    """Best and worst closed cycles side by side, plus the evidence behind a one-line profile."""
    w = _wallet(session, address)
    wid = w["id"]
    cols = """coin, direction, opened_at, closed_at, entry_px, exit_px, max_notional, holding_hours,
              realized_pnl - fees AS net, scale"""
    best = session.execute(text(f"""
        SELECT {cols} FROM position_cycle WHERE wallet_id = :w AND status <> 'open' AND complete
          AND realized_pnl - fees > 0 ORDER BY realized_pnl - fees DESC LIMIT 3"""), {"w": wid}).mappings().all()
    worst = session.execute(text(f"""
        SELECT {cols} FROM position_cycle WHERE wallet_id = :w AND status <> 'open' AND complete
          AND realized_pnl - fees < 0 ORDER BY realized_pnl - fees ASC LIMIT 2"""), {"w": wid}).mappings().all()
    agg = session.execute(text("""
        SELECT count(*) AS n, count(*) FILTER (WHERE realized_pnl - fees > 0) AS wins,
               sum(realized_pnl - fees) AS net,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY holding_hours) AS median_hours,
               avg((direction = 'long')::int) AS long_share,
               mode() WITHIN GROUP (ORDER BY scale) AS main_scale
        FROM position_cycle WHERE wallet_id = :w AND status <> 'open' AND complete"""), {"w": wid}).mappings().one()
    coins = session.execute(text("""
        SELECT coin, count(*) AS n FROM position_cycle WHERE wallet_id = :w AND status <> 'open'
        GROUP BY coin ORDER BY 2 DESC LIMIT 3"""), {"w": wid}).all()
    if w["venue"] == "okx":
        liq = session.execute(text("SELECT count(*) FROM okx_position WHERE wallet_id = :w AND liquidated"),
                              {"w": wid}).scalar_one()
    else:
        f = session.execute(text("""SELECT value, n FROM factor_value
            WHERE wallet_id = :w AND factor = 'liq_rate' AND time_window = 'all'"""), {"w": wid}).first()
        liq = round(float(f.value) * f.n) if f and f.value is not None else None
    timing = session.execute(text("""
        SELECT scale, time_window, q_value, edge_shrunk, n_scored, p_value FROM timing_score
        WHERE wallet_id = :w AND version = :v AND status = 'scored' AND coin_group = 'all' AND q_value IS NOT NULL
        ORDER BY q_value ASC, edge_shrunk DESC LIMIT 1"""), {"w": wid, "v": TIMING_VERSION}).mappings().first()
    styles = session.execute(text("""
        SELECT style, status FROM style_tag WHERE wallet_id = :w AND status IN ('verified', 'candidate')
        ORDER BY array_position(ARRAY['verified', 'candidate'], status::text), score DESC NULLS LAST"""),
        {"w": wid}).mappings().all()
    factors = {f: {"value": num(v), "n": n} for f, v, n in session.execute(text("""
        SELECT factor, value, n FROM factor_value WHERE wallet_id = :w AND time_window = 'all'
          AND factor IN ('dd_ratio', 'profit_factor', 'win_rate_lo', 'max_dd_usd')"""), {"w": wid}).all()}
    n = agg["n"] or 0
    return {
        "venue": w["venue"],
        "closed": n, "wins": agg["wins"] or 0, "win_rate": (agg["wins"] or 0) / n if n else None,
        "net": num(agg["net"]), "median_hours": num(agg["median_hours"]), "long_share": num(agg["long_share"]),
        "main_scale": agg["main_scale"], "top_coins": [{"coin": c, "n": k} for c, k in coins],
        "liquidations": liq,
        "timing": {**dict(timing), **{k: num(timing[k]) for k in ("q_value", "edge_shrunk", "p_value")}} if timing else None,
        "styles": [dict(s) for s in styles], "factors": factors,
        "best": [_cycle(r) for r in best], "worst": [_cycle(r) for r in worst],
    }


@router.get("/wallets/{address}/follow")
def follow_backtest(address: str, session: Session = Depends(get_session), since: date | None = None,
                    capital: float = Query(10_000, gt=0, le=1e9)):
    """What `capital` would have become following this account's flow-adjusted return (time-weighted, deposits and
    withdrawals removed) since `since`. Hyperliquid only: OKX publishes no equity history. Hindsight, before copy
    delays, slippage and fees of the follower; the account's own fees and funding are already in its PnL."""
    w = _wallet(session, address)
    if w["venue"] != "hyperliquid":
        return {"supported": False, "reason": "no_equity_history"}
    pts: dict[datetime, Point] = {}
    for series in ("allTime", "month"):  # month is denser; it overrides allTime where both have a point
        for ts, av, pnl in session.execute(text("""
            SELECT ts, account_value, pnl FROM equity_point WHERE wallet_id = :w AND series = :s ORDER BY ts"""),
                {"w": w["id"], "s": series}).all():
            pts[ts] = Point(ts, float(av), float(pnl))
    points = sorted(pts.values(), key=lambda p: p.ts)
    if len(points) < 3:
        return {"supported": True, "rows": [], "reason": "insufficient_points"}
    start = datetime.combine(since, datetime.min.time(), UTC) if since else datetime.now(UTC) - timedelta(days=90)
    path = index_path(points, start)
    if len(path) < 3:
        return {"supported": True, "rows": [], "reason": "insufficient_points"}
    peak, mdd = 1.0, 0.0
    for _, v in path:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    steps = [(b[0] - a[0]).total_seconds() / 3600 for a, b in zip(path, path[1:])]
    return {
        "supported": True, "capital": capital, "since": path[0][0], "until": path[-1][0],
        "covered": path[0][0] <= start + timedelta(days=2),
        "final": capital * path[-1][1], "ret": path[-1][1] - 1, "mdd": mdd, "n_points": len(path),
        "median_step_hours": median(steps) if steps else None,
        "rows": [[t, capital * v] for t, v in path],
    }


def _daily_realized(session: Session, wid: int, since: datetime) -> dict[str, float]:
    return {str(d): float(v) for d, v in session.execute(text("""
        SELECT closed_at::date, sum(realized_pnl - fees) FROM position_cycle
        WHERE wallet_id = :w AND status <> 'open' AND closed_at >= :s GROUP BY 1 ORDER BY 1"""),
        {"w": wid, "s": since}).all()}


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 10:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


@router.get("/compare")
def compare(session: Session = Depends(get_session), addr: list[str] = Query(..., min_length=1, max_length=4),
            days: int = Query(180, ge=30, le=730)):
    """Up to four traders side by side: headline factors, best timing score, styles, cumulative realized PnL from
    closed cycles (same rule for both venues) and the correlation of their daily realized PnL."""
    since = datetime.now(UTC) - timedelta(days=days)
    people = []
    for a in addr:
        w = _wallet(session, a)
        h = wallet_highlights(w["address"], session)
        daily = _daily_realized(session, w["id"], since)
        cum, run = [], 0.0
        for d in sorted(daily):
            run += daily[d]
            cum.append([d, run])
        people.append({"address": w["address"], "display_name": w["display_name"], "venue": w["venue"],
                       "size": num(w["asset"] if w["venue"] == "okx" else w["account_value"]),
                       "highlights": {k: h[k] for k in ("closed", "win_rate", "net", "median_hours", "long_share",
                                                        "main_scale", "top_coins", "liquidations", "timing",
                                                        "styles", "factors")},
                       "curve": cum, "_daily": daily})
    matrix: list[list[float | None]] = []
    for p in people:
        row = []
        for q in people:
            if p is q:
                row.append(1.0)
                continue
            days_all = sorted(set(p["_daily"]) | set(q["_daily"]))
            row.append(_corr([p["_daily"].get(d, 0.0) for d in days_all], [q["_daily"].get(d, 0.0) for d in days_all]))
        matrix.append(row)
    for p in people:
        p.pop("_daily")
    return {"days": days, "since": since, "traders": people, "correlation": matrix}


INTERACTIVE_DELAY_LIMIT = 3000  # decisions: above this the delay curve is left to the nightly batch


@router.get("/wallets/{address}/backtest")
def wallet_backtest(address: str, session: Session = Depends(get_session), days: int = Query(30, ge=3, le=365),
                    ratio: float = Query(100, gt=0, le=1000), delay: int = Query(30, ge=0, le=86400),
                    capital: float = Query(10_000, gt=0, le=1e9),
                    leverage_mode: Literal["account", "leader"] = "account",
                    max_leverage: float = Query(10, gt=0, le=MAX_LEVERAGE)):
    """Historical copy backtest (see app/sim/backtest.py for every assumption). ratio is the copy ratio in percent."""
    w = _wallet(session, address)
    p = BacktestParams(days=days, capital=capital, ratio=ratio / 100, delay_seconds=delay,
                       leverage_mode=leverage_mode, max_leverage=max_leverage)
    n = session.execute(text("""SELECT count(*) FROM trade_decision WHERE wallet_id = :w
                                AND started_at >= now() - make_interval(days => :d)"""), {"w": w["id"], "d": days}).scalar_one()
    r = backtest(session, w["id"], p, delays=DELAYS if n <= INTERACTIVE_DELAY_LIMIT else ())
    if not r.get("ok"):
        return r
    stored = session.execute(text("""SELECT delay_curve FROM copy_backtest WHERE wallet_id = :w AND window_days = 30
                                     AND params_key = 'default-v2' AND trades > 0"""), {"w": w["id"]}).scalar()
    return {**r, "delay_curve": r.get("delay_curve") or stored, "delay_curve_source": "live" if r.get("delay_curve")
            else ("nightly_30d" if stored else None)}


@router.get("/wallets/{address}/behavior")
def wallet_behavior(address: str, session: Session = Depends(get_session)):
    w = _wallet(session, address)
    return fingerprint(session, w["id"], w["venue"])


@router.get("/wallets/{address}/copyability")
def wallet_copyability(address: str, session: Session = Depends(get_session)):
    """Stored copyability score, trader health and the nightly default copy backtests (7 / 30 / 90 days)."""
    w = _wallet(session, address)
    score = session.execute(text("SELECT status, score, grade, components, computed_at FROM copy_score WHERE wallet_id = :w"),
                            {"w": w["id"]}).mappings().first()
    h = session.execute(text("""SELECT status, prev_status, signals, changed_at, computed_at FROM trader_health
                                WHERE wallet_id = :w"""), {"w": w["id"]}).mappings().first()
    bts = session.execute(text("""
        SELECT window_days, ret, pnl, mdd, leader_ret, retention, trades, liquidations, costs, approx_equity, computed_at
        FROM copy_backtest WHERE wallet_id = :w AND params_key = 'default-v2' ORDER BY window_days"""),
        {"w": w["id"]}).mappings().all()
    audit=session.execute(text("""SELECT window_days,source_cutoff,version,outcome,published_at FROM copy_replay_audit
        WHERE wallet_id=:w ORDER BY source_cutoff DESC,window_days"""),{"w":w["id"]}).mappings()
    nums = ("ret", "pnl", "mdd", "leader_ret", "retention", "costs")
    return {"score": {**dict(score), "score": num(score["score"])} if score else None,
            "health": dict(h) if h else None, "replay_audit":[dict(r) for r in audit],
            "backtests": [{**dict(b), **{k: num(b[k]) for k in nums},
                           "execution_status": "executed" if b["trades"] else "no_executions"} for b in bts]}


@router.get("/copy-board")
def copy_board(session: Session = Depends(get_session), window: int = Query(30, description="7, 30 or 90"),
               sort: Literal["ret", "pnl", "score", "mdd", "retention"] = "ret", venue: VenueFilter = "all",
               limit: int = Query(100, ge=1, le=500), min_trades: int = Query(3, ge=0)):
    """Who would have been most profitable to copy: the nightly default backtests (100% ratio, 30 s delay, fees and
    slippage, 10x cap), with copyability score and health."""
    if window not in (7, 30, 90):
        raise HTTPException(422, "window must be 7, 30 or 90")
    order = {"ret": "b.ret DESC", "pnl": "b.pnl DESC", "score": "s.score DESC NULLS LAST", "mdd": "b.mdd DESC",
             "retention": "b.retention DESC NULLS LAST"}[sort]
    rows = session.execute(text(f"""
        SELECT w.address, w.display_name, w.venue, b.ret, b.pnl, b.mdd, b.leader_ret, b.retention, b.trades,
               b.liquidations, b.costs, b.approx_equity, b.curve, b.computed_at,
               (SELECT d.delay_curve FROM copy_backtest d WHERE d.wallet_id = b.wallet_id AND d.window_days = 30
                  AND d.params_key = b.params_key AND d.trades > 0) AS delay_curve,
               s.score, s.grade, s.status AS score_status, h.status AS health
        FROM copy_backtest b JOIN wallet w ON w.id = b.wallet_id
        LEFT JOIN copy_score s ON s.wallet_id = b.wallet_id LEFT JOIN trader_health h ON h.wallet_id = b.wallet_id
        WHERE b.window_days = :win AND b.params_key = 'default-v2' AND b.trades >= :mt{venue_clause(venue)}
        ORDER BY {order} LIMIT :n"""), {"win": window, "mt": max(1, min_trades), "n": limit}).mappings().all()
    computed = session.execute(text(
        "SELECT max(computed_at) FROM copy_backtest WHERE params_key = 'default-v2' AND window_days = :win AND trades > 0"
    ), {"win": window}).scalar()
    nums = ("ret", "pnl", "mdd", "leader_ret", "retention", "costs", "score")
    return {"window": window, "sort": sort, "computed_at": computed,
            "rows": [{**dict(r), **{k: num(r[k]) for k in nums}} for r in rows]}
