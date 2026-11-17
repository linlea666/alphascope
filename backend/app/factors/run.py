"""Compute factors and style labels for every trader with closed cycles (both venues) and store them (docs/13)."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.factors.compute import FACTOR_VERSION, Cyc, Extras, compute
from app.factors.styles import STYLE_VERSION, STYLES, evaluate, forward_check, oos_cut
from app.research.timing import TIMING_VERSION

log = logging.getLogger(__name__)


def load_extras(s: Session, cut: datetime, now: datetime) -> dict[int, dict[str, Extras]]:
    """Per wallet, the extras of each window: all, 90d and pre (before the out-of-sample cut)."""
    base = dict(s.execute(text("""
        SELECT w.id, coalesce(w.account_value, t.asset,
               (SELECT r.asset FROM okx_leaderboard_row r JOIN okx_leaderboard_run u ON u.id = r.run_id
                WHERE r.wallet_id = w.id ORDER BY u.business_date DESC LIMIT 1))
        FROM wallet w LEFT JOIN okx_trader t ON t.wallet_id = w.id
        WHERE w.id IN (SELECT DISTINCT wallet_id FROM position_cycle)""")).all())
    liq = {r.wallet_id: r for r in s.execute(text("""
        SELECT wallet_id, count(*) FILTER (WHERE liquidated) AS all_n,
               count(*) FILTER (WHERE liquidated AND close_time > :d90) AS n90,
               count(*) FILTER (WHERE liquidated AND close_time <= :cut) AS pre,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY lever) AS lev
        FROM okx_position WHERE close_type <> 'partClose' GROUP BY wallet_id"""),
        {"d90": now - timedelta(days=90), "cut": cut}).all()}
    # Hyperliquid: liquidation fills where this wallet is the liquidated user, one event per coin and minute
    for r in s.execute(text("""
        WITH ev AS (
          SELECT DISTINCT f.wallet_id, f.coin, date_trunc('minute', f.time) AS t FROM fill f
          JOIN wallet w ON w.id = f.wallet_id
          WHERE f.liquidation IS NOT NULL AND lower(f.liquidation->>'liquidatedUser') = w.address)
        SELECT wallet_id, count(*) AS all_n, count(*) FILTER (WHERE t > :d90) AS n90,
               count(*) FILTER (WHERE t <= :cut) AS pre, NULL::numeric AS lev
        FROM ev GROUP BY wallet_id"""), {"d90": now - timedelta(days=90), "cut": cut}).all():
        liq[r.wallet_id] = r
    hl_archived = set(s.execute(text("SELECT wallet_id FROM fill_sync WHERE n_fills > 0")).scalars())
    timing: dict[int, dict] = {}
    for r in s.execute(text("""
        SELECT wallet_id, scale, edge_shrunk, q_value, buy_location, sell_location, buy_extreme, sell_extreme,
               null_extreme, n_buy, n_sell, n_scored
        FROM timing_score WHERE time_window = 'all' AND coin_group = 'all' AND status = 'scored' AND version = :v"""),
            {"v": TIMING_VERSION}).all():
        f = lambda x: None if x is None else float(x)  # noqa: E731
        timing.setdefault(r.wallet_id, {})[r.scale] = {
            "edge": f(r.edge_shrunk), "q": f(r.q_value), "buy_loc": f(r.buy_location), "sell_loc": f(r.sell_location),
            "buy_extreme": f(r.buy_extreme), "sell_extreme": f(r.sell_extreme), "null_extreme": f(r.null_extreme),
            "n_buy": r.n_buy or 0, "n_sell": r.n_sell or 0, "n": r.n_scored or 0}
    capture = {r.wallet_id: (float(r.avg), r.n) for r in s.execute(text("""
        SELECT wallet_id, avg(capture_ratio) AS avg, count(*) AS n FROM leg_capture
        WHERE capture_ratio IS NOT NULL AND max_notional > 0 GROUP BY wallet_id""")).all()}
    # unrealized PnL of the latest radar poll (traders never polled stay unknown)
    opened = {r.wallet_id: (float(r.upl), r.n) for r in s.execute(text("""
        SELECT p.wallet_id, coalesce(sum(t.upl), 0) AS upl, count(t.coin) AS n
        FROM position_watch p LEFT JOIN trader_position t ON t.wallet_id = p.wallet_id
        WHERE p.last_observed_at IS NOT NULL GROUP BY p.wallet_id""")).all()}
    out: dict[int, dict[str, Extras]] = {}
    for wid in base:
        b = float(base[wid]) if base[wid] is not None else None
        lq = liq.get(wid)
        if lq is None and wid in hl_archived:  # archived fills without a liquidation of this wallet: zero, not unknown
            lq = type("Z", (), {"all_n": 0, "n90": 0, "pre": 0, "lev": None})()
        cap = capture.get(wid)
        lev = float(lq.lev) if lq is not None and lq.lev is not None else None
        out[wid] = {
            "all": Extras(b, lq.all_n if lq else None, lev, timing.get(wid), cap[0] if cap else None,
                          cap[1] if cap else 0, *(opened.get(wid) or (None, 0))),
            "90d": Extras(b, lq.n90 if lq else None, lev),
            "pre": Extras(b, lq.pre if lq else None, lev),
        }
    return out


def run_styles(sf: sessionmaker[Session], now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    cut = oos_cut(now)
    d90 = now - timedelta(days=90)
    with sf() as s:
        extras = load_extras(s, cut, now)
        venues = dict(s.execute(text("SELECT id, venue FROM wallet WHERE id = ANY(:ids)"),
                                {"ids": list(extras)}).all())
        rows = s.execute(text("""
            SELECT wallet_id, coin, direction, opened_at, closed_at, scale, max_notional, realized_pnl - fees,
                   holding_hours
            FROM position_cycle WHERE closed_at IS NOT NULL AND status <> 'open' AND complete
            ORDER BY wallet_id, closed_at""")).yield_per(20000)
        by_wallet: dict[int, list[Cyc]] = {}
        for wid, coin, d, o, c, scale, ntl, net, h in rows:
            by_wallet.setdefault(wid, []).append(Cyc(coin, d, o, c, scale, float(ntl or 0), float(net or 0),
                                                     float(h or 0)))
    factor_rows: list[tuple] = []
    tags: list[dict] = []
    counts: dict[str, dict[str, int]] = {}
    for wid, cs in by_wallet.items():
        ex = extras.get(wid) or {"all": Extras(), "90d": Extras(), "pre": Extras()}
        f_all, meta = compute(cs, ex["all"], now)
        f_90, _ = compute([c for c in cs if c.closed_at > d90], ex["90d"], now)
        f_pre, meta_pre = compute([c for c in cs if c.closed_at <= cut], ex["pre"], cut)
        for window, f in (("all", f_all), ("90d", f_90)):
            for k, (v, n) in f.items():
                factor_rows.append((wid, k, window, FACTOR_VERSION, v, n, now))
        for st in STYLES:
            ev = evaluate(st, f_all)
            if ev["status"] in ("none", "insufficient"):
                continue
            oos = None
            status = ev["status"]
            if st.forward and not st.risk_flag:
                pre = evaluate(st, f_pre, as_of_cut=True)
                if pre["status"] == "candidate":
                    oos = forward_check(st, cs, cut, meta_pre.get("top_coin"))
                    if oos["passed"] and status == "candidate":
                        status = "verified"
                else:
                    oos = {"cut": cut.isoformat(), "tested": False, "pre_status": pre["status"]}
            elif not st.risk_flag:
                oos = {"tested": False, "reason": "not_point_in_time"}
            evidence = ev["evidence"] + ([{"meta": "top_coin", "value": meta.get("top_coin")}]
                                         if st.key == "specialist" else [])
            tags.append({"w": wid, "st": st.key, "v": STYLE_VERSION, "status": status, "conf": ev["confidence"],
                         "score": ev["score"], "n": ev["n"], "ev": json.dumps(evidence, default=str),
                         "oos": json.dumps(oos, default=str) if oos else None, "now": now})
            counts.setdefault(st.key, {}).setdefault(status, 0)
            counts[st.key][status] += 1
    with sf() as s:
        s.execute(text("DELETE FROM factor_value"))
        s.execute(text("DELETE FROM style_tag"))
        pg = s.connection().connection.driver_connection
        with pg.cursor() as cur, cur.copy("COPY factor_value (wallet_id, factor, time_window, version, value, n, as_of) "
                                          "FROM STDIN") as copy:
            for r in factor_rows:
                copy.write_row(r)
        if tags:
            s.execute(text("""
                INSERT INTO style_tag (wallet_id, style, version, status, confidence, score, n, evidence, oos,
                                       computed_at)
                VALUES (:w, :st, :v, :status, :conf, :score, :n, CAST(:ev AS jsonb), CAST(:oos AS jsonb), :now)"""),
                tags)
        s.commit()
    by_venue: dict[str, int] = {}
    for wid in by_wallet:
        by_venue[venues.get(wid, "?")] = by_venue.get(venues.get(wid, "?"), 0) + 1
    return {"traders": len(by_wallet), "by_venue": by_venue, "factors": len(factor_rows), "tags": len(tags),
            "styles": counts}
