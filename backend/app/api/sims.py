"""Copy simulations (S3/S4): create / control simulations and read their results and analytics."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import settings_store
from app.api.common import num
from app.api.watch import _queue_fills
from app.db import get_session
from app.sim import analytics as an
from app.traders import parse_trader_key

router = APIRouter(prefix="/api/v1/sims", tags=["sims"])
OWNER = "default"
MAX_RUNNING = 50


class SimParams(BaseModel):
    sizing: Literal["equity_ratio", "fixed_multiple", "fixed_cap"] = "equity_ratio"
    size_factor: float = Field(1.0, gt=0, le=100)
    cap_per_coin: float | None = Field(None, gt=0)
    max_leverage: float = Field(10, gt=0, le=50)
    coins_include: list[str] = Field(default_factory=list, max_length=100)
    coins_exclude: list[str] = Field(default_factory=list, max_length=100)
    follow_adjust: bool = True
    delay_seconds: int = Field(0, ge=0, le=3600)
    fee_bps: float = Field(4.5, ge=0, le=100)
    slippage_bps: float = Field(3, ge=0, le=500)
    min_trade_usd: float = Field(10, ge=0)
    rebalance_pct: float = Field(5, ge=0, le=100)
    stop_drawdown_pct: float | None = Field(None, gt=0, lt=100)
    # account: one cross-margined account; leader: each position isolated at the leader's leverage on that coin
    leverage_mode: Literal["account", "leader"] = "account"

    @model_validator(mode="after")
    def _cap(self):
        if self.sizing == "fixed_cap" and not self.cap_per_coin:
            raise ValueError("cap_per_coin is required for fixed_cap")
        return self


class SimCreate(SimParams):
    name: str = Field(min_length=1, max_length=64)
    leader: str
    capital: float = Field(10_000, gt=0, le=1e9)
    copy_existing: bool = False


class SimPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    action: Literal["pause", "resume", "stop"] | None = None
    params: SimParams | None = None


def _clean_coins(v: list[str]) -> list[str]:
    return sorted({c.strip() for c in v if c.strip()})


def _get(session: Session, sim_id: int):
    row = session.execute(text("SELECT * FROM copy_sim WHERE id = :id AND owner = :o"),
                          {"id": sim_id, "o": OWNER}).mappings().first()
    if row is None:
        raise HTTPException(404, "simulation not found")
    return row


SUMMARY_SQL = """
    SELECT c.id, c.name, c.status, c.stop_reason, c.capital, c.cash, c.peak_equity, c.sizing, c.size_factor,
           c.max_leverage, c.delay_seconds, c.leverage_mode, c.created_at, c.stopped_at, w.address AS leader,
           coalesce((c.state ->> 'stop_requested')::boolean, false) AS stop_pending,
           coalesce(a.alias, w.display_name) AS leader_name,
           e.equity, e.t AS equity_at, e.gross_exposure,
           (SELECT count(*) FROM sim_trade t WHERE t.sim_id = c.id AND t.kind <> 'funding') AS trades,
           (SELECT coalesce(sum(fee), 0) FROM sim_trade t WHERE t.sim_id = c.id) AS fees,
           (SELECT coalesce(sum(slippage_cost), 0) FROM sim_trade t WHERE t.sim_id = c.id) AS slippage,
           (SELECT coalesce(sum(funding), 0) FROM sim_trade t WHERE t.sim_id = c.id) AS funding,
           (SELECT least(min(equity / NULLIF(greatest(peak, c.capital), 0)) - 1, 0) FROM (
               SELECT equity, max(equity) OVER (ORDER BY t) AS peak FROM sim_equity q WHERE q.sim_id = c.id) x) AS max_dd,
           (SELECT count(*) FROM sim_position p WHERE p.sim_id = c.id) AS open_positions,
           (SELECT count(*) FROM sim_trade t WHERE t.sim_id = c.id AND t.kind = 'liquidation') AS liquidations,
           (SELECT status FROM trader_health h WHERE h.wallet_id = w.id) AS leader_health
    FROM copy_sim c JOIN wallet w ON w.id = c.leader_wallet_id
    LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = c.owner
    LEFT JOIN LATERAL (SELECT equity, t, gross_exposure FROM sim_equity q WHERE q.sim_id = c.id ORDER BY t DESC LIMIT 1) e
      ON true
    WHERE c.owner = :o"""

NUMS = ("capital", "cash", "peak_equity", "size_factor", "max_leverage", "equity", "gross_exposure", "fees",
        "slippage", "funding", "max_dd")


def _summary(r) -> dict:
    d = {**dict(r), **{k: num(r[k]) for k in NUMS if k in r}}
    equity = d["equity"] if d["equity"] is not None else d["capital"]
    d["equity"] = equity
    d["pnl"] = equity - d["capital"]
    d["return"] = d["pnl"] / d["capital"] if d["capital"] else None
    return d


@router.get("")
def list_sims(session: Session = Depends(get_session)):
    rows = session.execute(text(SUMMARY_SQL + " ORDER BY c.status = 'stopped', c.created_at DESC"),
                           {"o": OWNER}).mappings().all()
    sims = [_summary(r) for r in rows]
    running = [s for s in sims if s["status"] != "stopped"]
    return {
        "sims": sims,
        "portfolio": {
            "count": len(sims), "active": len(running),
            "capital": sum(s["capital"] for s in running), "equity": sum(s["equity"] for s in running),
            "pnl": sum(s["pnl"] for s in running),
        },
    }


@router.post("")
def create_sim(body: SimCreate, session: Session = Depends(get_session)):
    leader = parse_trader_key(body.leader) or ''
    if not leader:
        raise HTTPException(422, "invalid leader address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": leader}).scalar()
    if wid is None:
        raise HTTPException(404, "leader wallet not in database")
    running = session.execute(text("SELECT count(*) FROM copy_sim WHERE owner = :o AND status <> 'stopped'"),
                              {"o": OWNER}).scalar_one()
    if running >= MAX_RUNNING:
        raise HTTPException(409, f"at most {MAX_RUNNING} active simulations")
    sid = session.execute(text("""
        INSERT INTO copy_sim (owner, name, leader_wallet_id, status, capital, sizing, size_factor, cap_per_coin,
          max_leverage, coins_include, coins_exclude, follow_adjust, copy_existing, delay_seconds, fee_bps, slippage_bps,
          min_trade_usd, rebalance_pct, stop_drawdown_pct, leverage_mode, cash, peak_equity, state)
        VALUES (:o, :name, :w, 'running', :cap, :sizing, :sf, :cpc, :ml, :inc, :exc, :fa, :ce, :delay, :fee, :slip,
          :mt, :rb, :dd, :lm, :cap, :cap, '{}')
        RETURNING id"""),
        {"o": OWNER, "name": body.name.strip(), "w": wid, "cap": body.capital, "sizing": body.sizing,
         "sf": body.size_factor, "cpc": body.cap_per_coin, "ml": body.max_leverage,
         "inc": _clean_coins(body.coins_include), "exc": _clean_coins(body.coins_exclude), "fa": body.follow_adjust,
         "ce": body.copy_existing, "delay": body.delay_seconds, "fee": body.fee_bps, "slip": body.slippage_bps,
         "mt": body.min_trade_usd, "rb": body.rebalance_pct, "dd": body.stop_drawdown_pct,
         "lm": body.leverage_mode}).scalar_one()
    _queue_fills(session, wid)  # the leader's fills feed replay / timing for comparison
    session.commit()
    return {"id": sid}


def _tz(session: Session) -> str:
    return settings_store.get(session, "live").get("timezone") or "UTC"


TRADES_SQL = """
    SELECT sim_id, id, t, coin, kind, size, px, fee, slippage_cost, funding, realized_pnl, position_after, reason, mae
    FROM sim_trade WHERE sim_id = ANY(:ids) ORDER BY t, id"""


def _daily_closes(session: Session, ids: list[int], tz: str) -> dict[int, list]:
    out: dict[int, list] = {i: [] for i in ids}
    for r in session.execute(text("""
        SELECT DISTINCT ON (sim_id, d) sim_id, d, equity FROM (
          SELECT sim_id, (t AT TIME ZONE :tz)::date AS d, t, equity FROM sim_equity WHERE sim_id = ANY(:ids)) x
        ORDER BY sim_id, d, t DESC"""), {"ids": ids, "tz": tz}).all():
        out[r.sim_id].append((r.d, float(r.equity)))
    return out


@router.get("/dashboard")
def dashboard(period: Literal["1d", "7d", "30d", "all"] = "7d", session: Session = Depends(get_session)):
    """Every simulation over one period, the combined daily PnL calendar and the correlation of daily returns."""
    now = datetime.now(UTC)
    tz = _tz(session)
    sims = [_summary(r) for r in session.execute(text(SUMMARY_SQL + " ORDER BY c.created_at"), {"o": OWNER})
            .mappings().all()]
    ids = [s["id"] for s in sims]
    equity: dict[int, list] = {i: [] for i in ids}
    for r in session.execute(text("""
        SELECT sim_id, t AS at, equity, leader_equity FROM sim_equity WHERE sim_id = ANY(:ids) ORDER BY sim_id, t"""),
            {"ids": ids}).all():
        equity[r.sim_id].append((r.at, float(r.equity), an.as_float(r.leader_equity)))
    trades: dict[int, list] = {i: [] for i in ids}
    for r in session.execute(text(TRADES_SQL), {"ids": ids}).mappings().all():
        trades[r["sim_id"]].append(dict(r))
    closes = _daily_closes(session, ids, tz)
    rows, daily_by_sim, calendar = [], {}, {}
    for s in sims:
        start = an.period_start(period, now, s["created_at"])
        seg = [t for t in trades[s["id"]] if t["t"] > start]
        stats = an.period_stats(equity[s["id"]], s["capital"], start)
        cyc = an.cycle_stats(an.cycles(trades[s["id"]]), since=start)
        rows.append({**s, "period": {**stats, **cyc,
                                     "trades": sum(t["kind"] != "funding" for t in seg),
                                     "liquidations": sum(t["kind"] == "liquidation" for t in seg),
                                     "fees": sum(float(t["fee"] or 0) for t in seg),
                                     "slippage": sum(float(t["slippage_cost"] or 0) for t in seg),
                                     "funding": sum(float(t["funding"] or 0) for t in seg)}})
        days = an.daily_pnl(closes[s["id"]], s["capital"])
        daily_by_sim[s["id"]] = {d["day"]: d["return"] for d in days if d["return"] is not None}
        for d in days:
            c = calendar.setdefault(d["day"], {"day": d["day"], "pnl": 0.0, "sims": 0})
            c["pnl"] += d["pnl"]
            c["sims"] += 1
    active = [r for r in rows if r["status"] != "stopped"]
    return {
        "period": period,
        "timezone": tz,
        "sims": rows,
        "portfolio": {
            "count": len(rows), "active": len(active),
            "capital": sum(r["capital"] for r in rows), "equity": sum(r["equity"] for r in rows),
            "pnl": sum(r["pnl"] for r in rows),
            "period_pnl": sum(r["period"]["pnl"] for r in rows),
            "period_fees": sum(r["period"]["fees"] for r in rows),
            "period_funding": sum(r["period"]["funding"] for r in rows),
            "period_slippage": sum(r["period"]["slippage"] for r in rows),
            "period_trades": sum(r["period"]["trades"] for r in rows),
        },
        "calendar": sorted(calendar.values(), key=lambda c: c["day"]),
        "correlation": an.correlation(daily_by_sim),
    }


@router.patch("/{sim_id}")
def update_sim(sim_id: int, body: SimPatch, session: Session = Depends(get_session)):
    sim = _get(session, sim_id)
    if sim["status"] == "stopped" and (body.action or body.params):
        raise HTTPException(409, "simulation is stopped")
    if body.name:
        session.execute(text("UPDATE copy_sim SET name = :n WHERE id = :id"), {"n": body.name.strip(), "id": sim_id})
    if body.action == "pause":
        session.execute(text("UPDATE copy_sim SET status = 'paused' WHERE id = :id"), {"id": sim_id})
    elif body.action == "resume":
        session.execute(text("UPDATE copy_sim SET status = 'running' WHERE id = :id"), {"id": sim_id})
    elif body.action == "stop":
        # the live process closes positions at its next cycle's real prices, then marks the sim stopped
        session.execute(text("""
            UPDATE copy_sim SET status = CASE WHEN status = 'paused' THEN 'running' ELSE status END,
              state = jsonb_set(coalesce(state, '{}'), '{stop_requested}', 'true') WHERE id = :id"""), {"id": sim_id})
    if body.params:
        p = body.params
        session.execute(text("""
            UPDATE copy_sim SET sizing = :sizing, size_factor = :sf, cap_per_coin = :cpc, max_leverage = :ml,
              coins_include = :inc, coins_exclude = :exc, follow_adjust = :fa, delay_seconds = :delay, fee_bps = :fee,
              slippage_bps = :slip, min_trade_usd = :mt, rebalance_pct = :rb, stop_drawdown_pct = :dd,
              leverage_mode = :lm WHERE id = :id"""),
            {"sizing": p.sizing, "sf": p.size_factor, "cpc": p.cap_per_coin, "ml": p.max_leverage,
             "inc": _clean_coins(p.coins_include), "exc": _clean_coins(p.coins_exclude), "fa": p.follow_adjust,
             "delay": p.delay_seconds, "fee": p.fee_bps, "slip": p.slippage_bps, "mt": p.min_trade_usd,
             "rb": p.rebalance_pct, "dd": p.stop_drawdown_pct, "lm": p.leverage_mode, "id": sim_id})
    session.commit()
    return {"ok": True}


@router.delete("/{sim_id}")
def delete_sim(sim_id: int, session: Session = Depends(get_session)):
    _get(session, sim_id)
    session.execute(text("DELETE FROM copy_sim WHERE id = :id"), {"id": sim_id})
    session.commit()
    return {"ok": True}


@router.get("/{sim_id}")
def sim_detail(sim_id: int, session: Session = Depends(get_session), trades: int = Query(200, ge=1, le=2000)):
    sim = _get(session, sim_id)
    summary = _summary(session.execute(text(SUMMARY_SQL + " AND c.id = :id"), {"o": OWNER, "id": sim_id})
                       .mappings().one())
    params = {k: (num(sim[k]) if isinstance(sim[k], Decimal) else sim[k]) for k in (
        "sizing", "size_factor", "cap_per_coin", "max_leverage", "coins_include", "coins_exclude", "follow_adjust",
        "copy_existing", "delay_seconds", "fee_bps", "slippage_bps", "min_trade_usd", "rebalance_pct",
        "stop_drawdown_pct", "leverage_mode")}
    positions = [{"coin": r.coin, "size": num(r.size), "entry_px": num(r.entry_px), "opened_at": r.opened_at,
                  "mark": num(r.mark_px), "leverage": num(r.leverage), "liq_px": num(r.liq_px), "margin": num(r.margin),
                  "worst_upnl": num(r.worst_upnl), "best_upnl": num(r.best_upnl)} for r in session.execute(text("""
                      SELECT coin, size, entry_px, opened_at, mark_px, leverage, liq_px, margin, worst_upnl, best_upnl
                      FROM sim_position WHERE sim_id = :s ORDER BY coin"""), {"s": sim_id}).all()]
    trade_rows = session.execute(text("""
        SELECT t, coin, kind, side, size, mid, px, fee, slippage_cost, funding, realized_pnl, position_after,
               leader_szi, reason, mae FROM sim_trade WHERE sim_id = :s ORDER BY t DESC, id DESC LIMIT :n"""),
        {"s": sim_id, "n": trades}).mappings().all()
    equity = session.execute(text("""
        SELECT t AS at, equity, gross_exposure, leader_equity FROM sim_equity WHERE sim_id = :s ORDER BY t"""),
        {"s": sim_id}).all()
    leader_pos = [{"coin": r.coin, "szi": num(r.szi), "value": num(r.position_value), "leverage": r.leverage,
                   "liq_px": num(r.liquidation_px)} for r in session.execute(text("""
        SELECT coin, szi, position_value, leverage, liquidation_px FROM live_position WHERE wallet_id = :w
        ORDER BY abs(position_value) DESC"""), {"w": sim["leader_wallet_id"]}).all()]
    tz = _tz(session)
    eq_series = [(e.at, float(e.equity), an.as_float(e.leader_equity)) for e in equity]
    all_trades = [dict(r) for r in session.execute(text(TRADES_SQL), {"ids": [sim_id]}).mappings().all()]
    cs = an.cycles(all_trades)
    now = datetime.now(UTC)
    analytics = {
        "timezone": tz,
        "periods": {k: {**an.period_stats(eq_series, summary["capital"], an.period_start(k, now, sim["created_at"])),
                        **an.cycle_stats(cs, since=an.period_start(k, now, sim["created_at"]))}
                    for k in an.PERIODS},
        "daily": an.daily_pnl(_daily_closes(session, [sim_id], tz)[sim_id], summary["capital"]),
        "per_coin": an.per_coin(all_trades, positions),
        "cycles": [{"coin": c.coin, "side": c.side, "opened_at": c.opened_at, "closed_at": c.closed_at,
                    "pnl": c.pnl, "fees": c.fees, "funding": c.funding, "max_notional": c.max_notional,
                    "trades": c.trades, "mae": c.mae, "liquidated": c.liquidated, "leader_liquidated": c.leader_liquidated}
                   for c in sorted(cs, key=lambda c: c.opened_at, reverse=True)[:100]],
    }
    return {
        "summary": summary,
        "analytics": analytics,
        "params": params,
        "state": {k: v for k, v in (sim["state"] or {}).items() if k in ("followed", "baseline", "pending", "started_at")},
        "positions": positions,
        "leader_positions": leader_pos,
        "trades": [{**dict(t), **{k: num(t[k]) for k in ("size", "mid", "px", "fee", "slippage_cost", "funding",
                                                         "realized_pnl", "position_after", "leader_szi", "mae")}}
                   for t in trade_rows],
        "equity": [[e.at, float(e.equity), float(e.gross_exposure), num(e.leader_equity)] for e in equity],
    }
