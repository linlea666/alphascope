"""Nightly copy analysis (M4): copy backtests for a candidate pool, then copyability scores and trader health, and
an email when a watched trader's health changes for the worse. Everything is recomputable from the archive."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.alerts.mailer import send
from app.factors import copy_validation, copyability, health
from app.research.timing import TIMING_VERSION
from app.sim.backtest import DELAYS, BacktestParams, PriceBook, backtest
from app.traders import trader_label

log = logging.getLogger(__name__)

WINDOWS = (7, 30, 90)
PARAMS_KEY = "default-v2"  # 100% ratio, 30 s delay, account margin, 10x cap, 4.5 bp fee, 3 bp slippage
POOL_LIMIT = 600
ALERT_STATUSES = {"declining", "dormant", "drifting"}
OWNER = "default"


def candidate_pool(session: Session, now: datetime, limit: int = POOL_LIMIT) -> list[int]:
    """Watched and simulated traders first, then style-labelled, timing-significant and OKX-board traders, all with
    decisions in the last 90 days."""
    rows = session.execute(text("""
        WITH c AS (
          SELECT wallet_id, 0 AS pri FROM watch_item
          UNION ALL SELECT leader_wallet_id, 0 FROM copy_sim WHERE status <> 'stopped'
          UNION ALL SELECT wallet_id, 1 FROM style_tag WHERE status IN ('verified', 'candidate')
          UNION ALL SELECT wallet_id, 2 FROM timing_score WHERE status = 'scored' AND q_value < 0.1
          UNION ALL SELECT wallet_id, 3 FROM okx_presence WHERE time_window = '7d' AND currently_seen)
        SELECT wallet_id FROM c
        WHERE wallet_id IN (SELECT DISTINCT wallet_id FROM trade_decision WHERE started_at >= :s)
        GROUP BY wallet_id ORDER BY min(pri), wallet_id LIMIT :n"""),
        {"s": now - timedelta(days=90), "n": limit}).scalars().all()
    return list(rows)


def _store_backtest(s: Session, wid: int, days: int, r: dict, now: datetime) -> None:
    if not r.get("ok"):
        s.execute(text("DELETE FROM copy_backtest WHERE wallet_id = :w AND window_days = :d AND params_key = :k AND computed_at<=:t"),
                  {"w": wid, "d": days, "k": PARAMS_KEY,"t":now})
        return
    s.execute(text("""
        INSERT INTO copy_backtest (wallet_id, window_days, params_key, computed_at, capital, ret, pnl, mdd, leader_ret,
          retention, trades, liquidations, costs, n_decisions, approx_equity, delay_curve, curve)
        VALUES (:w, :d, :k, :t, :cap, :ret, :pnl, :mdd, :lr, :rt, :tr, :liq, :costs, :n, :ap, CAST(:dc AS jsonb),
                CAST(:cv AS jsonb))
        ON CONFLICT (wallet_id, window_days, params_key) DO UPDATE SET computed_at = EXCLUDED.computed_at,
          capital = EXCLUDED.capital, ret = EXCLUDED.ret, pnl = EXCLUDED.pnl, mdd = EXCLUDED.mdd,
          leader_ret = EXCLUDED.leader_ret, retention = EXCLUDED.retention, trades = EXCLUDED.trades,
          liquidations = EXCLUDED.liquidations, costs = EXCLUDED.costs, n_decisions = EXCLUDED.n_decisions,
          approx_equity = EXCLUDED.approx_equity, delay_curve = EXCLUDED.delay_curve, curve = EXCLUDED.curve
        WHERE copy_backtest.computed_at<=EXCLUDED.computed_at"""),
        {"w": wid, "d": days, "k": PARAMS_KEY, "t": now, "cap": r["capital"], "ret": r["ret"], "pnl": r["pnl"],
         "mdd": r["mdd"], "lr": r["leader_ret"], "rt": r["retention"], "tr": r["trades"], "liq": r["liquidations"],
         "costs": r["costs"], "n": r["n_decisions"], "ap": r["approx_equity"],
         "dc": json.dumps(r.get("delay_curve")), "cv": json.dumps(r["curve"][-40:] if days == 7 else r["curve"])})


def score_wallet(s: Session, wid: int, now: datetime) -> dict:
    f = {k: (float(v) if v is not None else None) for k, v in s.execute(text("""
        SELECT factor, value FROM factor_value WHERE wallet_id = :w AND time_window = 'all'"""), {"w": wid}).all()}
    t = s.execute(text("""
        SELECT q_value, edge_shrunk FROM timing_score WHERE wallet_id = :w AND version = :v AND status = 'scored'
          AND coin_group = 'all' AND q_value IS NOT NULL ORDER BY q_value, edge_shrunk DESC LIMIT 1"""),
        {"w": wid, "v": TIMING_VERSION}).first()
    verified = bool(s.execute(text("""SELECT 1 FROM style_tag WHERE wallet_id = :w AND status = 'verified'
                                      AND style <> 'high_risk'"""), {"w": wid}).first())
    rows = s.execute(text("""
        SELECT window_days, ret, mdd, delay_curve FROM copy_backtest
        WHERE wallet_id = :w AND params_key = :k AND trades > 0"""),
        {"w": wid, "k": PARAMS_KEY}).mappings()
    bts = {row["window_days"]: dict(row) for row in rows}
    for b in bts.values():
        b["ret"], b["mdd"] = float(b["ret"]), float(b["mdd"] or 0)
    res = copyability.score(f, float(t.q_value) if t else None, float(t.edge_shrunk) if t and t.edge_shrunk is not None
                            else None, verified, bts.get(30), bts.get(90))
    s.execute(text("""
        INSERT INTO copy_score (wallet_id, status, score, grade, components, computed_at)
        VALUES (:w, :st, :sc, :g, CAST(:c AS jsonb), :t)
        ON CONFLICT (wallet_id) DO UPDATE SET status = EXCLUDED.status, score = EXCLUDED.score, grade = EXCLUDED.grade,
          components = EXCLUDED.components, computed_at = EXCLUDED.computed_at"""),
        {"w": wid, "st": res["status"], "sc": res["score"], "g": res["grade"], "c": json.dumps(res["components"]),
         "t": now})
    copy_validation.record(s, wid, res, now)
    return res


def health_wallet(s: Session, wid: int, now: datetime) -> tuple[str, str | None]:
    cycles = [dict(r) for r in s.execute(text("""
        SELECT closed_at, (realized_pnl - fees)::float AS net, holding_hours::float AS hours, scale, direction,
               coalesce(max_notional, 0)::float AS notional
        FROM position_cycle WHERE wallet_id = :w AND status <> 'open' AND complete AND closed_at >= :s"""),
        {"w": wid, "s": now - timedelta(days=health.RECENT_DAYS + health.PRIOR_DAYS)}).mappings().all()]
    last_open = s.execute(text("SELECT max(opened_at) FROM position_cycle WHERE wallet_id = :w"), {"w": wid}).scalar()
    res = health.assess(cycles, last_open, now)
    prev = s.execute(text("SELECT status FROM trader_health WHERE wallet_id = :w"), {"w": wid}).scalar()
    s.execute(text("""
        INSERT INTO trader_health (wallet_id, status, prev_status, signals, changed_at, computed_at)
        VALUES (:w, :st, :prev, CAST(:sig AS jsonb), :t, :t)
        ON CONFLICT (wallet_id) DO UPDATE SET prev_status = trader_health.status, status = EXCLUDED.status,
          signals = EXCLUDED.signals, computed_at = EXCLUDED.computed_at,
          changed_at = CASE WHEN trader_health.status <> EXCLUDED.status THEN EXCLUDED.computed_at
                            ELSE trader_health.changed_at END,
          notified_at = CASE WHEN trader_health.status <> EXCLUDED.status THEN NULL ELSE trader_health.notified_at END"""),
        {"w": wid, "st": res["status"], "prev": prev, "sig": json.dumps(res["signals"], default=str), "t": now})
    return res["status"], prev


def notify_changes(s: Session, now: datetime, sender=send) -> str:
    """One email listing watched / simulated / alerted traders whose health turned bad and was not reported yet."""
    rows = s.execute(text("""
        SELECT h.wallet_id, h.status, h.prev_status, h.signals, w.address, coalesce(a.alias, w.display_name) AS name
        FROM trader_health h JOIN wallet w ON w.id = h.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        WHERE h.notified_at IS NULL AND h.changed_at IS NOT NULL AND h.status = ANY(:st)
          AND (h.wallet_id IN (SELECT wallet_id FROM watch_item) OR h.wallet_id IN (SELECT wallet_id FROM alert_subscription)
               OR h.wallet_id IN (SELECT leader_wallet_id FROM copy_sim WHERE status <> 'stopped'))"""),
        {"o": OWNER, "st": sorted(ALERT_STATUSES)}).mappings().all()
    if not rows:
        return "none"
    smtp = settings_store.get(s, "smtp")
    password = settings_store.get_secret(s, "smtp")
    status = "not_configured"
    if smtp.get("host") and smtp.get("username") and password and smtp.get("recipients"):
        labels = {"declining": "走下坡", "dormant": "沉寂", "drifting": "风格漂移"}
        lines = [f"AlphaScope：{len(rows)} 位关注的交易员状态变化", ""]
        for r in rows:
            link = f"{(smtp.get('public_url') or '').rstrip('/')}/zh/trader/{r['address']}" if smtp.get("public_url") else r["address"]
            keys = "、".join(x["key"] for x in r["signals"]) or "—"
            lines.append(f"· {trader_label(r['name'], r['address'])}：{labels.get(r['status'], r['status'])}（原因：{keys}）{link}")
        lines += ["", "近 30 天对比此前 180 天的已平仓位；只是提示，请到交易员页查看证据。"]
        try:
            sender(smtp, password, list(smtp["recipients"]), f"[AlphaScope] {len(rows)} 位交易员状态变化", "\n".join(lines))
            status = "sent"
        except Exception:  # noqa: BLE001
            status = "failed"
    s.execute(text("UPDATE trader_health SET notified_at = :t WHERE wallet_id = ANY(:w)"),
              {"t": now, "w": [r["wallet_id"] for r in rows]})
    return status


def run_copy_analysis(sf: sessionmaker[Session], now: datetime | None = None, limit: int = POOL_LIMIT,
                      sender=send) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        pool = candidate_pool(s, now, limit)
        prices = PriceBook(s, now - timedelta(days=max(WINDOWS)), now)
        ok = 0
        for wid in pool:
            try:
                for days in WINDOWS:
                    r = backtest(s, wid, BacktestParams(days=days), now=now, prices=prices,
                                 delays=DELAYS if days == 30 else ())
                    _store_backtest(s, wid, days, r, now)
                    ok += days == 30 and r.get("trades", 0) > 0
                s.commit()
            except Exception:  # noqa: BLE001 - one trader's bad data must not stop the batch
                s.rollback()
                log.exception("copy backtest failed for wallet %s", wid)
        scored = sum(score_wallet(s, wid, now)["status"] == "scored" for wid in pool)
        s.commit()
        statuses: dict[str, int] = {}
        for wid in pool:
            st, _ = health_wallet(s, wid, now)
            statuses[st] = statuses.get(st, 0) + 1
        s.commit()
        mail = notify_changes(s, now, sender)
        s.commit()
        validated = copy_validation.settle(s, now)
    return {"pool": len(pool), "backtested_30d": ok, "scored": scored, "health": statuses, "email": mail,
            "validation_outcomes": validated}

def replay_copy_history(sf):
    """Reprice legacy backtests at their original cutoffs. Newer scheduled runs keep precedence."""
    with sf() as s:
        rows=s.execute(text("""
            SELECT a.wallet_id,a.window_days,a.computed_at,a.ret,a.trades FROM copy_backtest a
            LEFT JOIN copy_replay_audit b ON b.wallet_id=a.wallet_id AND b.window_days=a.window_days AND b.source_cutoff=a.computed_at AND b.version=:version
            WHERE a.params_key='default' AND b.wallet_id IS NULL
            ORDER BY a.wallet_id,a.window_days
        """),{"version":PARAMS_KEY}).all()
        if rows:
            prices=PriceBook(s,min(r.computed_at-timedelta(days=r.window_days) for r in rows),max(r.computed_at for r in rows))
        counts={}
        for wid,days,cutoff,old_ret,old_trades in rows:
            result=backtest(s,wid,BacktestParams(days=days),now=cutoff,prices=prices,delays=DELAYS if days==30 else ())
            _store_backtest(s,wid,days,result,cutoff)
            flag="recalculated" if result.get("ok") else result.get("reason","unavailable")
            outcome={"status":flag,"previous_ret":float(old_ret) if old_ret is not None else None,"previous_trades":old_trades,
                     **{k:result[k] for k in ("ret","trades","basis","approx_equity","n_decisions","equity_source") if k in result}}
            s.execute(text("""INSERT INTO copy_replay_audit(wallet_id,window_days,source_cutoff,version,outcome)
                      VALUES(:w,:d,:t,:v,CAST(:o AS jsonb)) ON CONFLICT DO NOTHING"""),
                      {"w":wid,"d":days,"t":cutoff,"v":PARAMS_KEY,"o":json.dumps(outcome)})
            counts[flag]=counts.get(flag,0)+1
            s.commit()
        wallets=s.scalars(text("SELECT wallet_id FROM copy_score UNION SELECT DISTINCT wallet_id FROM copy_backtest")).all()
        now=datetime.now(UTC)
        for wid in wallets:
            score_wallet(s,wid,now)
        s.commit()
    return {"legacy_windows":len(rows),"outcomes":counts,"scores_recomputed":len(wallets)}
