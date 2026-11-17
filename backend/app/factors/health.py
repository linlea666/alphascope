"""Trader health (h1): the last 30 days against the 180 days before, from closed position cycles.

Status (first rule that holds): dormant (traded before, nothing opened in 14 days), declining (two or more results
clearly worse), drifting (the way of trading changed: scalp / short share, holding time or size), hot (clearly better
results), stable. Too few cycles on either side -> insufficient. Every signal carries recent and prior values."""

from datetime import datetime, timedelta
from statistics import median

from app.api.wallets import wilson

RECENT_DAYS, PRIOR_DAYS, DORMANT_DAYS = 30, 180, 14
MIN_RECENT, MIN_PRIOR = 8, 20


def _stats(cs: list[dict]) -> dict:
    nets = [c["net"] for c in cs]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    lo = wilson(len(wins), len(nets))
    return {
        "n": len(cs),
        "win_lo": lo[0] if lo else None,
        "win_rate": len(wins) / len(nets) if nets else None,
        "pf": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "net": sum(nets),
        "avg_loss": -sum(losses) / len(losses) if losses else None,
        "max_loss": -min(losses) if losses else None,
        "hold_h": median(c["hours"] for c in cs) if cs else None,
        "scalp": sum(c["scale"] == "scalp" for c in cs) / len(cs) if cs else None,
        "short": sum(c["direction"] == "short" for c in cs) / len(cs) if cs else None,
        "size": median(c["notional"] for c in cs) if cs else None,
    }


def assess(cycles: list[dict], last_open: datetime | None, now: datetime) -> dict:
    """cycles: closed cycles with closed_at, net, hours, scale, direction, notional."""
    cut = now - timedelta(days=RECENT_DAYS)
    recent = [c for c in cycles if c["closed_at"] >= cut]
    prior = [c for c in cycles if now - timedelta(days=RECENT_DAYS + PRIOR_DAYS) <= c["closed_at"] < cut]
    r, p = _stats(recent), _stats(prior)
    signals = []

    def sig(key: str, rv, pv) -> None:
        signals.append({"key": key, "recent": rv, "prior": pv})

    if (last_open is None or last_open < now - timedelta(days=DORMANT_DAYS)) and p["n"] + r["n"] >= 10:
        sig("dormant", (now - last_open).days if last_open else None, DORMANT_DAYS)
        return {"status": "dormant", "signals": signals, "recent": r, "prior": p}
    if r["n"] < MIN_RECENT or p["n"] < MIN_PRIOR:
        return {"status": "insufficient", "signals": signals, "recent": r, "prior": p}
    worse = 0
    if r["win_rate"] is not None and p["win_rate"] is not None and r["win_rate"] <= p["win_rate"] - 0.15:
        worse += 1
        sig("win_rate_down", r["win_rate"], p["win_rate"])
    if p["pf"] is not None and p["pf"] >= 1.3 and (r["pf"] is None or r["pf"] < 1):
        worse += 1
        sig("pf_down", r["pf"], p["pf"])
    if p["net"] > 0 and r["net"] < 0:
        worse += 1
        sig("net_negative", r["net"], p["net"] / p["n"] * r["n"])
    if r["max_loss"] and p["avg_loss"] and r["max_loss"] > 3 * p["avg_loss"]:
        worse += 1
        sig("big_loss", r["max_loss"], p["avg_loss"])
    drift = []
    for key, thr in (("scalp", 0.3), ("short", 0.3)):
        if r[key] is not None and p[key] is not None and abs(r[key] - p[key]) >= thr:
            drift.append(key)
            sig(f"{key}_shift", r[key], p[key])
    if r["hold_h"] and p["hold_h"] and not (1 / 3 <= r["hold_h"] / p["hold_h"] <= 3):
        drift.append("hold")
        sig("hold_shift", r["hold_h"], p["hold_h"])
    if r["size"] and p["size"] and r["size"] / p["size"] > 2.5:
        drift.append("size")
        sig("size_up", r["size"], p["size"])
    if worse >= 2:
        status = "declining"
    elif drift:
        status = "drifting"
    elif (r["pf"] or 0) >= 2 and r["net"] > 0 and r["win_rate"] is not None and p["win_rate"] is not None \
            and r["win_rate"] >= p["win_rate"] + 0.1:
        status = "hot"
        sig("hot", r["pf"], p["pf"])
    else:
        status = "stable"
    return {"status": status, "signals": signals, "recent": r, "prior": p}
