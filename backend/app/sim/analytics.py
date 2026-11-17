"""Copy-simulation analytics: period returns, daily PnL, round-trip cycles, per-coin breakdown, correlation."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np

PERIODS = {"1d": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}


def period_start(period: str, now: datetime, created_at: datetime) -> datetime:
    span = PERIODS[period]
    return created_at if span is None else max(created_at, now - span)


def period_stats(equity: list[tuple[datetime, float, float | None]], capital: float, start: datetime) -> dict:
    """Return, PnL and max drawdown from `start` on. equity: (t, equity, leader_equity) sorted by t.

    The opening value is the last snapshot at or before `start` (capital if the sim started later).
    Leader return uses the leader's account value, which includes deposits and withdrawals.
    """
    before = [e for e in equity if e[0] <= start]
    inside = [e for e in equity if e[0] > start]
    open_eq = before[-1][1] if before else capital
    open_lead = before[-1][2] if before else (inside[0][2] if inside else None)
    path = [open_eq] + [e[1] for e in inside]
    close_eq = path[-1]
    times = [start] + [e[0] for e in inside]
    peak, mdd = path[0], 0.0
    under_since, longest = None, timedelta(0)
    for t, v in zip(times, path):
        if v >= peak:
            peak, under_since = v, None
        elif under_since is None:
            under_since = t
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
        if under_since is not None:
            longest = max(longest, t - under_since)
    close_lead = inside[-1][2] if inside else open_lead
    lead_ret = (close_lead / open_lead - 1) if open_lead and close_lead is not None and open_lead > 0 else None
    ret = close_eq / open_eq - 1 if open_eq > 0 else None
    return {
        "pnl": close_eq - open_eq,
        "return": ret,
        "max_dd": mdd,
        "current_dd": close_eq / peak - 1 if peak > 0 else 0.0,  # below the period's peak now
        "max_dd_days": longest.total_seconds() / 86400,  # longest time spent below a previous peak
        "leader_return": lead_ret,
        # what share of the leader's move the copy kept (only meaningful when the leader moved)
        "retention": ret / lead_ret if ret is not None and lead_ret and abs(lead_ret) >= 0.005 else None,
    }


def daily_pnl(closes: list[tuple[date, float]], capital: float, first_day: date | None = None) -> list[dict]:
    """closes: (local day, last equity of that day) sorted. PnL of a day = close - previous close (capital first)."""
    out, prev = [], capital
    for d, eq in closes:
        if first_day and d < first_day:
            prev = eq
            continue
        out.append({"day": d.isoformat(), "pnl": eq - prev, "return": eq / prev - 1 if prev > 0 else None,
                    "equity": eq})
        prev = eq
    return out


@dataclass
class Cycle:
    coin: str
    side: int
    opened_at: datetime
    closed_at: datetime | None = None
    pnl: float = 0.0  # realized - fees - funding
    fees: float = 0.0
    funding: float = 0.0
    max_notional: float = 0.0
    trades: int = 0
    reasons: set[str] = field(default_factory=set)
    mae: float | None = None  # worst unrealized PnL while held (from the closing trade)
    liquidated: bool = False
    leader_liquidated: bool = False


def cycles(trades: list[dict]) -> list[Cycle]:
    """Round trips per coin from flat to flat (a flip closes one cycle and opens the next).

    trades: sim_trade rows sorted by (t, id) with t, coin, kind, size, px, fee, funding, realized_pnl, position_after.
    """
    open_: dict[str, Cycle] = {}
    done: list[Cycle] = []
    for tr in trades:
        coin, after = tr["coin"], float(tr["position_after"] or 0)
        c = open_.get(coin)
        if tr["kind"] == "funding":
            if c:
                c.funding += float(tr["funding"] or 0)
                c.pnl -= float(tr["funding"] or 0)
            continue
        realized, fee = float(tr["realized_pnl"] or 0), float(tr["fee"] or 0)
        before_sign = c.side if c else 0
        after_sign = (after > 0) - (after < 0)
        if c is None and after_sign:
            c = open_[coin] = Cycle(coin, after_sign, tr["t"])
        if c is None:
            continue
        flip = before_sign and after_sign and after_sign != before_sign
        # on a flip the fee covers both legs; attribute it by size to the closing leg and the new one
        if flip:
            size = float(tr["size"] or 0)
            close_share = (size - abs(after)) / size if size else 1.0
            c.pnl += realized - fee * close_share
            c.fees += fee * close_share
            fee = fee * (1 - close_share)
            realized = 0.0
        else:
            c.pnl += realized - fee
            c.fees += fee
            fee = 0.0
        c.trades += 1
        c.reasons.add(tr["kind"])
        c.max_notional = max(c.max_notional, abs(after) * float(tr["px"] or 0))
        if after_sign != before_sign and before_sign:
            c.closed_at = tr["t"]
            c.mae = float(tr["mae"]) if tr.get("mae") is not None else None
            c.liquidated = tr["kind"] == "liquidation"
            c.leader_liquidated = tr.get("reason") == "leader_liquidated"
            done.append(open_.pop(coin))
            if flip:
                n = open_[coin] = Cycle(coin, after_sign, tr["t"], pnl=-fee, fees=fee, trades=1)
                n.max_notional = abs(after) * float(tr["px"] or 0)
    return done + list(open_.values())


def cycle_stats(cs: list[Cycle], since: datetime | None = None) -> dict:
    closed = [c for c in cs if c.closed_at and (since is None or c.closed_at >= since)]
    wins = [c.pnl for c in closed if c.pnl > 0]
    losses = [c.pnl for c in closed if c.pnl <= 0]
    hold = [(c.closed_at - c.opened_at).total_seconds() / 3600 for c in closed]
    return {
        "closed": len(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "median_hold_hours": float(np.median(hold)) if hold else None,
        "worst_mae": min((c.mae for c in closed if c.mae is not None), default=None),
        "avg_mae": float(np.mean([c.mae for c in closed if c.mae is not None])) if any(c.mae is not None for c in closed) else None,
        "liquidated": sum(c.liquidated for c in closed),
    }


def per_coin(trades: list[dict], positions: list[dict]) -> list[dict]:
    """Realized, fees, slippage, funding and current unrealized per coin."""
    rows: dict[str, dict] = {}
    for tr in trades:
        r = rows.setdefault(tr["coin"], {"coin": tr["coin"], "realized": 0.0, "fees": 0.0, "slippage": 0.0,
                                         "funding": 0.0, "unrealized": 0.0, "trades": 0})
        r["realized"] += float(tr["realized_pnl"] or 0)
        r["fees"] += float(tr["fee"] or 0)
        r["slippage"] += float(tr["slippage_cost"] or 0)
        r["funding"] += float(tr["funding"] or 0)
        r["trades"] += tr["kind"] != "funding"
    for p in positions:
        if p.get("mark") is not None:
            r = rows.setdefault(p["coin"], {"coin": p["coin"], "realized": 0.0, "fees": 0.0, "slippage": 0.0,
                                            "funding": 0.0, "unrealized": 0.0, "trades": 0})
            r["unrealized"] = float(p["size"]) * (float(p["mark"]) - float(p["entry_px"]))
    for r in rows.values():
        r["net"] = r["realized"] + r["unrealized"] - r["fees"] - r["funding"]
    return sorted(rows.values(), key=lambda r: r["net"], reverse=True)


def correlation(daily: dict[int, dict[str, float]], min_days: int = 5) -> dict:
    """Pearson correlation of daily returns between simulations over their common days."""
    ids = sorted(daily)
    matrix: list[list[float | None]] = []
    for a in ids:
        row: list[float | None] = []
        for b in ids:
            days = sorted(set(daily[a]) & set(daily[b]))
            if a == b:
                row.append(1.0)
                continue
            if len(days) < min_days:
                row.append(None)
                continue
            x = np.array([daily[a][d] for d in days])
            y = np.array([daily[b][d] for d in days])
            row.append(float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None)
        matrix.append(row)
    return {"ids": ids, "matrix": matrix, "min_days": min_days}


def as_float(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)
