"""Copyability score (c1): can a follower actually earn what this trader earns? 0-100, from seven parts, each kept
with the value and sample behind it. Traders with too little evidence are "insufficient", never scored.

Parts (max points): skill 20 (timing significance, out-of-sample verified styles), copy return 20 (historical copy
backtest after costs, 90d else 30d), drawdown 15 (backtest max drawdown, realized drawdown / profit), risk 15
(liquidation rate, median leverage), latency 15 (return kept with a 5-minute copy delay), sample & activity 10,
concentration 5 (share of profit from the best position)."""

import math

SCORE_VERSION = "c1"
MIN_CLOSED = 15
GRADES = ((80, "A"), (65, "B"), (50, "C"), (0, "D"))


def _lin(x: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi (either direction), clamped."""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def score(f: dict, timing_q: float | None, timing_edge: float | None, verified: bool, bt30: dict | None,
          bt90: dict | None) -> dict:
    """f: factor values ({name: value}); bt*: copy backtest rows (ret, mdd, delay_curve) or None."""
    n_closed = f.get("n_closed") or 0
    bt = bt90 or bt30
    if n_closed < MIN_CLOSED or bt is None:
        return {"status": "insufficient", "score": None, "grade": None,
                "components": [{"key": "sample", "value": n_closed, "need": MIN_CLOSED, "backtest": bt is not None}]}
    parts = []

    def part(key: str, pts: float, mx: int, **evidence) -> None:
        parts.append({"key": key, "points": round(pts, 1), "max": mx, **evidence})

    # skill: significant timing beats anything; an out-of-sample verified style adds
    if timing_q is not None and timing_q < 0.01:
        skill = 16
    elif timing_q is not None and timing_q < 0.05:
        skill = 13
    elif timing_q is not None and timing_q < 0.1:
        skill = 10
    else:
        skill = 4 if (timing_edge or 0) > 0 else 0
    part("skill", min(20, skill + (4 if verified else 0)), 20, q=timing_q, edge=timing_edge, verified=verified)
    # copy return: -10% -> 0, 0% -> 8, +50% -> 20
    r = bt["ret"]
    pts = 8 * _lin(r, -0.1, 0) if r < 0 else 8 + 12 * _lin(r, 0, 0.5)
    part("copy_return", pts, 20, ret=r, window=90 if bt90 else 30)
    # drawdown: backtest max drawdown 0 -> full, -50% -> 0; realized drawdown / profit 0.1 -> full, 1 -> 0
    dd = 7.5 * (1 - _lin(-(bt["mdd"] or 0), 0, 0.5))
    ratio = f.get("dd_ratio")
    dd += 7.5 * (1 - _lin(ratio, 0.1, 1.0)) if ratio is not None else 3.75
    part("drawdown", dd, 15, mdd=bt["mdd"], dd_ratio=ratio)
    # risk: liquidation rate 0 -> 10, >= 5% -> 0; median leverage <= 5 -> 5, >= 25 -> 0
    liq, lev = f.get("liq_rate"), f.get("median_leverage")
    risk = (10 * (1 - _lin(liq, 0, 0.05)) if liq is not None else 5) + (5 * (1 - _lin(lev, 5, 25)) if lev is not None else 2.5)
    part("risk", risk, 15, liq_rate=liq, median_leverage=lev)
    # latency: return kept at a 5-minute delay vs immediate (only meaningful when the immediate copy made money)
    curve = {int(d): v for d, v in (bt30 or bt).get("delay_curve") or [] if v is not None}
    r0, r5 = curve.get(0), curve.get(300)
    if r0 is not None and r5 is not None and r0 > 0.005:
        kept = r5 / r0
        part("latency", 15 * _lin(kept, 0, 0.9), 15, kept=kept, r0=r0, r5=r5)
    else:
        part("latency", 5, 15, kept=None, r0=r0, r5=r5)
    # sample and activity: 15 -> 0, 200+ closed positions -> 6 (log scale); traded within 3 days -> 4, 30+ days -> 0
    days = f.get("days_since_last")
    act = 6 * _lin(math.log(max(n_closed, 1)), math.log(MIN_CLOSED), math.log(200))
    act += 4 * (1 - _lin(days, 3, 30)) if days is not None else 0
    part("activity", act, 10, n_closed=n_closed, days_since_last=days)
    top1 = f.get("top1_share")
    part("concentration", 5 * (1 - _lin(top1, 0.2, 0.6)) if top1 is not None else 2.5, 5, top1_share=top1)
    total = sum(p["points"] for p in parts)
    grade = next(g for cut, g in GRADES if total >= cut)
    return {"status": "scored", "score": round(total, 1), "grade": grade, "components": parts}
