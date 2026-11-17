"""Style labels (rules s1): explainable threshold rules over factors, each result carrying its evidence (docs/13).

Status:
- insufficient: a sample requirement is not met (too few cycles, months, legs...)
- watch: all but one of three or more rules hold (or that one input is unknown)
- candidate: every rule holds on the full history
- verified: also passed out of sample — the rules held on data before the cut (OOS_DAYS ago) and the trader's
  later cycles of the same kind stayed profitable (>= OOS_MIN_FORWARD cycles, net > 0, profit factor >= 1).
Steady and low-drawdown also require the open book to be healthy (unrealized loss at the latest radar poll vs
account size): realized history alone rewards traders who never close losing positions. Traders the radar has not
polled are unknown there and stop at watch.
Timing-based styles cannot be recomputed as of the cut (timing scores are not point-in-time), so they stop at
candidate. high_risk is a risk flag, not an ability: any one rule triggers it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.factors.compute import Cyc

STYLE_VERSION = "s2"  # s2: ability gates (net profit, no loser-holding), forward check on all later cycles
OOS_DAYS = 90
OOS_MIN_FORWARD = 5


@dataclass
class Style:
    key: str
    requires: list[tuple[str, str, float]]  # sample requirements
    rules: list  # (factor, op, threshold) or {"any": [...]}
    score: str  # factor whose value ranks traders within the style (higher is better)
    forward: str | None = "all"  # which later cycles the OOS check uses: all | short | swing | top_coin | None
    risk_flag: bool = False
    note: str = field(default="")


STYLES: list[Style] = [
    Style("steady", [("n_closed", ">=", 30), ("months", ">=", 4)],
          [("positive_month_ratio", ">=", 0.6), ("profit_factor", ">=", 1.3), ("top1_share", "<=", 0.35),
           ("dd_ratio", "<=", 0.35), ("liq_rate", "<=", 0.02), ("open_loss_ratio", "<=", 0.15)],
          "positive_month_ratio"),
    Style("trend", [("big_n", ">=", 5)],
          [("big_net", ">", 0), {"any": [("q_macro", "<", 0.1), ("q_swing", "<", 0.1), ("capture_avg", ">=", 0.3)]}],
          "capture_avg", forward=None),
    Style("swing", [("swing_n", ">=", 15)],
          [("swing_pf", ">=", 1.3), ("swing_win_lo", ">=", 0.4), ("swing_net", ">", 0)], "swing_pf", forward="swing"),
    Style("bottom", [("n_closed", ">=", 15)],
          [("buy_loc", ">=", 0.7), ("buy_lift", ">=", 1.5), ("long_net", ">", 0)], "buy_lift", forward=None),
    Style("top_short", [("n_closed", ">=", 15)],
          [("short_net", ">", 0), ("short_pf", ">=", 1.3), ("short_win_lo", ">=", 0.4), ("sell_loc", ">=", 0.65)],
          "short_pf", forward="short"),
    Style("scalper", [("n_closed", ">=", 100), ("scalp_share", ">=", 0.6)],
          [("profit_factor", ">=", 1.2), ("net_pnl", ">", 0), ("win_rate_lo", ">=", 0.45)], "profit_factor",
          note="copy_hard"),
    Style("low_dd", [("n_closed", ">=", 20), ("months", ">=", 3)],
          [("dd_ratio", "<=", 0.1), ("net_pnl", ">", 0), ("liq_rate", "<=", 0), ("open_loss_ratio", "<=", 0.05)],
          "dd_ratio_inv"),
    Style("specialist", [("top_coin_n", ">=", 20)],
          [("top_coin_share", ">=", 0.6), ("top_coin_net", ">", 0), ("top_coin_pf", ">=", 1.3),
           ("top_coin_win_lo", ">=", 0.45)], "top_coin_pf", forward="top_coin"),
    Style("high_risk", [("n_closed", ">=", 10)],
          [{"any": [("liq_rate", ">=", 0.05), ("median_leverage", ">=", 30), ("top1_share", ">=", 0.6),
                    ("hold_ratio", ">=", 10)]}],
          "liq_rate", forward=None, risk_flag=True),
]
BY_KEY = {s.key: s for s in STYLES}
# Factors that only exist for the present (the open book at the latest poll): skipped when judging as of the cut
NOT_POINT_IN_TIME = {"open_loss_ratio"}
# Vetoes: a known failure removes the label entirely (no "watch" for someone sitting on a large open loss)
VETO = {"open_loss_ratio"}
# Gates for every ability style (not the risk flag), also vetoes when known:
# - the trader is profitable overall: a "verified swing trader" who loses money overall says nothing useful;
# - losers are not held 10x longer than winners: closing winners fast and sitting on losers inflates win rate and
#   profit factor, and pushes the losers into longer scales, so per-scale statistics look flawless.
# - no large open loss right now (> 30% of the account at the latest radar poll): realized records of traders who
#   never close losers look flawless.
GATES = [("net_pnl", ">", 0), ("hold_ratio", "<", 10), ("open_loss_ratio", "<=", 0.3)]
GATE_MIN_N = {"hold_ratio": 2}  # at least two closed losers (and winners) before the holding ratio can veto
OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _check(f: dict, rule) -> dict:
    if isinstance(rule, dict):
        parts = [_check(f, r) for r in rule["any"]]
        ok = True if any(p["ok"] for p in parts) else (None if all(p["ok"] is None for p in parts) else False)
        return {"any": parts, "ok": ok}
    name, op, thr = rule
    value, n = f.get(name, (None, 0))
    return {"factor": name, "op": op, "threshold": thr, "value": value, "n": n,
            "ok": None if value is None else bool(OPS[op](value, thr))}


def evaluate(style: Style, f: dict, as_of_cut: bool = False) -> dict:
    """Status, evidence and score of one style for one trader's factors (as_of_cut: judging past data only)."""
    req = [_check(f, r) for r in style.requires]
    gates = [] if style.risk_flag else [{**_check(f, g), "gate": True} for g in GATES]
    rules = [_check(f, r) for r in style.rules
             if not (as_of_cut and not isinstance(r, dict) and r[0] in NOT_POINT_IN_TIME)]
    n = int((f.get("n_closed") or (0, 0))[0] or 0)
    if not all(r["ok"] for r in req):
        status = "insufficient"
    elif any(r.get("factor") in VETO and r["ok"] is False for r in rules) or \
            any(g["ok"] is False and g["n"] >= GATE_MIN_N.get(g["factor"], 0) for g in gates):
        status = "none"
    elif style.risk_flag:
        status = "candidate" if any(r["ok"] for r in rules) else "none"
    else:
        passed = sum(1 for r in rules if r["ok"])
        # watch = one rule short, only meaningful for styles with at least three rules
        status = "candidate" if passed == len(rules) else \
            "watch" if len(rules) >= 3 and passed == len(rules) - 1 else "none"
    if style.score == "dd_ratio_inv":
        dd = (f.get("dd_ratio") or (None, 0))[0]
        score = None if dd is None else 1 - dd
    else:
        score = (f.get(style.score) or (None, 0))[0]
        if score is not None and (style.score.endswith("_pf") or style.score == "profit_factor"):
            score = min(score, 5.0)  # "no losing trade" (99) must not outrank a large, well-tested sample
    # confidence: grows with the sample relative to the style's requirement (3x the minimum = full)
    need = max((r["threshold"] for r in req if r.get("factor") in ("n_closed", "swing_n", "big_n", "top_coin_n")),
               default=10)
    have = max((r["value"] or 0 for r in req if r.get("factor") in ("n_closed", "swing_n", "big_n", "top_coin_n")),
               default=n)
    confidence = min(1.0, have / (3 * need)) if need else 0.0
    if score is not None and not style.risk_flag:
        score *= confidence
    return {"status": status, "evidence": req + gates + rules, "score": score, "confidence": confidence,
            "n": int(have)}


def forward_check(style: Style, cycles: list[Cyc], cut: datetime, top_coin: str | None) -> dict:
    """Later cycles of the style's kind (closed after the cut): did the edge persist? All later cycles together must
    also be profitable, so losses that migrated to another scale or coin still count against the trader."""
    later = [c for c in cycles if c.closed_at > cut]
    overall_net = sum(c.net for c in later)
    if style.forward == "short":
        later = [c for c in later if c.direction == "short"]
    elif style.forward == "swing":
        later = [c for c in later if c.scale == "swing"]
    elif style.forward == "top_coin":
        later = [c for c in later if c.coin == top_coin]
    nets = [c.net for c in later]
    gain, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    pf = (99.0 if gain > 0 else None) if loss == 0 else gain / loss
    passed = len(later) >= OOS_MIN_FORWARD and sum(nets) > 0 and pf is not None and pf >= 1 and overall_net > 0
    return {"cut": cut.isoformat(), "forward_n": len(later), "forward_net": sum(nets), "forward_pf": pf,
            "forward_all_net": overall_net, "passed": passed}


def oos_cut(now: datetime) -> datetime:
    return now - timedelta(days=OOS_DAYS)
