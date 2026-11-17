"""Factors of one trader from their closed position cycles (both venues), plus venue extras (docs/13).

Every factor is returned with the number of observations behind it, so a value from three trades is never mistaken
for one from three hundred. Missing inputs give None (unknown), never zero.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

FACTOR_VERSION = "f1"


@dataclass
class Cyc:
    coin: str
    direction: str  # long | short
    opened_at: datetime
    closed_at: datetime
    scale: str  # scalp | short | swing | macro
    notional: float  # peak notional
    net: float  # realized PnL after fees (and funding / penalties on OKX)
    hours: float


@dataclass
class Extras:
    """Inputs beyond cycles: account size for drawdown, OKX liquidations and leverage, timing and leg capture."""
    base_equity: float | None = None
    liquidations: int | None = None  # OKX only (positions closed by liquidation)
    median_leverage: float | None = None
    timing: dict | None = None  # scale -> {edge, q, buy_loc, sell_loc, buy_extreme, sell_extreme, null_extreme, n_buy, n_sell}
    capture_avg: float | None = None
    capture_legs: int = 0
    # Open positions at the latest radar poll: closed cycles alone flatter traders who never close losers
    open_upl: float | None = None
    open_positions: int = 0


def wilson_lo(wins: int, n: int, z: float = 1.96) -> float | None:
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / denom


def _pf(nets: list[float]) -> float | None:
    gain = sum(x for x in nets if x > 0)
    loss = -sum(x for x in nets if x < 0)
    if not nets:
        return None
    if loss == 0:
        return 99.0 if gain > 0 else None
    return gain / loss


def _group(cs: list[Cyc]) -> dict:
    nets = [c.net for c in cs]
    wins = sum(1 for x in nets if x > 0)
    return {"n": len(cs), "net": sum(nets), "pf": _pf(nets), "win_lo": wilson_lo(wins, len(cs)),
            "win": wins / len(cs) if cs else None}


def max_drawdown(cs: list[Cyc]) -> float:
    """Largest fall of cumulative realized PnL from its running peak (USD, >= 0), in close-time order."""
    peak = cum = dd = 0.0
    for c in sorted(cs, key=lambda c: c.closed_at):
        cum += c.net
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def compute(cs: list[Cyc], extras: Extras, now: datetime) -> tuple[dict[str, tuple[float | None, int]], dict]:
    """(factor -> (value, n), meta). cs: closed cycles of one trader in the window; meta carries e.g. the top coin."""
    out: dict[str, tuple[float | None, int]] = {}
    meta: dict = {}
    n = len(cs)
    put = lambda k, v, m=n: out.__setitem__(k, (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v), m))  # noqa: E731
    put("n_closed", n)
    if not cs:
        return out, meta
    g = _group(cs)
    put("net_pnl", g["net"])
    put("win_rate", g["win"])
    put("win_rate_lo", g["win_lo"])
    put("profit_factor", g["pf"])
    rets = [c.net / c.notional for c in cs if c.notional > 0]
    put("expectancy_bp", float(np.mean(rets)) * 1e4 if rets else None, len(rets))
    gains = sorted((c.net for c in cs if c.net > 0), reverse=True)
    total_gain = sum(gains)
    put("top1_share", gains[0] / total_gain if total_gain > 0 else None, len(gains))
    put("top5_share", sum(gains[:5]) / total_gain if total_gain > 0 else None, len(gains))
    win_h = [c.hours for c in cs if c.net > 0]
    loss_h = [c.hours for c in cs if c.net <= 0]
    put("hold_win_h", float(np.median(win_h)) if win_h else None, len(win_h))
    put("hold_loss_h", float(np.median(loss_h)) if loss_h else None, len(loss_h))
    put("hold_ratio", float(np.median(loss_h)) / float(np.median(win_h)) if win_h and loss_h and np.median(win_h) > 0
        else None, min(len(win_h), len(loss_h)))
    months: dict[str, float] = {}
    for c in cs:
        k = c.closed_at.strftime("%Y-%m")
        months[k] = months.get(k, 0.0) + c.net
    put("months", len(months), len(months))
    put("positive_month_ratio", sum(1 for v in months.values() if v > 0) / len(months), len(months))
    dd = max_drawdown(cs)
    put("max_dd_usd", dd)
    peak_cum = max(0.0, max(np.cumsum([c.net for c in sorted(cs, key=lambda c: c.closed_at)])))
    put("dd_ratio", dd / (extras.base_equity + peak_cum) if extras.base_equity and extras.base_equity > 0 else None)
    shorts = [c for c in cs if c.direction == "short"]
    longs = [c for c in cs if c.direction == "long"]
    s, lg = _group(shorts), _group(longs)
    put("short_share", len(shorts) / n)
    put("short_net", s["net"], s["n"])
    put("short_pf", s["pf"], s["n"])
    put("short_win_lo", s["win_lo"], s["n"])
    put("long_net", lg["net"], lg["n"])
    by_coin: dict[str, list[Cyc]] = {}
    for c in cs:
        by_coin.setdefault(c.coin, []).append(c)
    ntl = {k: sum(c.notional for c in v) for k, v in by_coin.items()}
    total_ntl = sum(ntl.values())
    if total_ntl > 0:
        put("coin_hhi", sum((v / total_ntl) ** 2 for v in ntl.values()))
        top = max(ntl, key=ntl.get)
        t = _group(by_coin[top])
        put("top_coin_share", ntl[top] / total_ntl)
        put("top_coin_n", t["n"], t["n"])
        put("top_coin_net", t["net"], t["n"])
        put("top_coin_pf", t["pf"], t["n"])
        put("top_coin_win_lo", t["win_lo"], t["n"])
        meta["top_coin"] = top
    scal = [c for c in cs if c.scale == "scalp"]
    swing = [c for c in cs if c.scale == "swing"]
    big = [c for c in cs if c.scale in ("swing", "macro")]
    sw, bg = _group(swing), _group(big)
    put("scalp_share", len(scal) / n)
    put("swing_n", sw["n"], sw["n"])
    put("swing_net", sw["net"], sw["n"])
    put("swing_pf", sw["pf"], sw["n"])
    put("swing_win_lo", sw["win_lo"], sw["n"])
    put("big_n", bg["n"], bg["n"])
    put("big_net", bg["net"], bg["n"])
    last = max(c.closed_at for c in cs)
    put("days_since_last", (now - last).total_seconds() / 86400)
    put("cycles_30d", sum(1 for c in cs if c.closed_at > now - timedelta(days=30)))
    if extras.liquidations is not None:
        put("liq_rate", extras.liquidations / n)
    if extras.median_leverage is not None:
        put("median_leverage", extras.median_leverage)
    tm = extras.timing or {}
    for scale in ("short", "swing", "macro"):
        t = tm.get(scale)
        if t:
            put(f"edge_{scale}", t.get("edge"), t.get("n") or 0)
            put(f"q_{scale}", t.get("q"), t.get("n") or 0)
    # buying lows / selling highs: the swing scale when it has enough buys, else the hourly one
    for side in ("buy", "sell"):
        best = max((tm.get(sc) for sc in ("swing", "short") if tm.get(sc) and (tm[sc].get(f"n_{side}") or 0) >= 10),
                   key=lambda t: t.get(f"n_{side}") or 0, default=None)
        if best:
            m = best.get(f"n_{side}") or 0
            put(f"{side}_loc", best.get(f"{side}_loc"), m)
            ext, null = best.get(f"{side}_extreme"), best.get("null_extreme")
            put(f"{side}_lift", ext / null if ext is not None and null else None, m)
    if extras.capture_legs:
        put("capture_avg", extras.capture_avg, extras.capture_legs)
    if extras.open_upl is not None:
        put("open_upl", extras.open_upl, extras.open_positions)
        put("open_loss_ratio", max(0.0, -extras.open_upl) / extras.base_equity
            if extras.base_equity and extras.base_equity > 0 else None, extras.open_positions)
    return out, meta
