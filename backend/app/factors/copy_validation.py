"""Does the copyability grade predict what copying earns next? Grades are frozen once per UTC day
(copy_score_history). After a horizon has passed, every trader graded that day is copied over the days that followed,
with the nightly default settings. Traders are ranked against others graded the same day, so a market-wide move
cancels out, and only grading days at least one horizon apart enter the test, so no two tested windows overlap."""

import json
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.factors.copyability import SCORE_VERSION
from app.sim.backtest import BacktestParams, PriceBook, backtest

HORIZONS = (7, 30)
MIN_TRADERS = 20  # graded traders with an outcome on one day
MIN_DAYS = 8  # independent grading days before a p-value is reported
HIGH, LOW = ("A", "B"), ("C", "D")


def record(s: Session, wid: int, res: dict, now: datetime) -> None:
    """Freeze today's grade. A rerun on the same UTC day replaces it; earlier days are never touched."""
    s.execute(text("""
        INSERT INTO copy_score_history (wallet_id, as_of, computed_at, version, status, score, grade, components)
        VALUES (:w, :d, :t, :v, :st, :sc, :g, CAST(:c AS jsonb))
        ON CONFLICT (wallet_id, as_of) DO UPDATE SET computed_at = EXCLUDED.computed_at, version = EXCLUDED.version,
          status = EXCLUDED.status, score = EXCLUDED.score, grade = EXCLUDED.grade, components = EXCLUDED.components
        WHERE NOT EXISTS (SELECT 1 FROM copy_score_outcome o WHERE o.wallet_id = EXCLUDED.wallet_id
                          AND o.as_of = EXCLUDED.as_of)"""),
        {"w": wid, "d": now.date(), "t": now, "v": SCORE_VERSION, "st": res["status"], "sc": res["score"],
         "g": res["grade"], "c": json.dumps(res["components"])})


def settle(s: Session, now: datetime, max_days: int = 3) -> int:
    """Copy each graded trader over the horizon after its grade, oldest grading days first. Returns outcomes stored."""
    pending = s.execute(text("""
        SELECT DISTINCT h.as_of, x.horizon FROM copy_score_history h CROSS JOIN unnest(CAST(:hs AS int[])) x(horizon)
        WHERE h.status = 'scored' AND h.computed_at + make_interval(days => x.horizon) <= :now
          AND NOT EXISTS (SELECT 1 FROM copy_score_outcome o WHERE o.wallet_id = h.wallet_id AND o.as_of = h.as_of
                          AND o.horizon_days = x.horizon)
        ORDER BY h.as_of, x.horizon LIMIT :n"""), {"hs": list(HORIZONS), "now": now, "n": max_days}).all()
    stored = 0
    for as_of, horizon in pending:
        rows = s.execute(text("""
            SELECT wallet_id, computed_at FROM copy_score_history h WHERE as_of = :d AND status = 'scored'
              AND NOT EXISTS (SELECT 1 FROM copy_score_outcome o WHERE o.wallet_id = h.wallet_id AND o.as_of = h.as_of
                              AND o.horizon_days = :h)"""), {"d": as_of, "h": horizon}).all()
        span = timedelta(days=horizon)
        prices = PriceBook(s, min(t for _, t in rows), max(t for _, t in rows) + span)
        for wid, graded in rows:
            r = backtest(s, wid, BacktestParams(days=horizon), now=graded + span, prices=prices)
            if r.get("ok"):
                ret, lead, trades, reason = r["ret"], r["leader_ret"], r["trades"], None
            elif r.get("reason") == "no_decisions":
                ret, lead, trades, reason = 0.0, None, 0, "no_decisions"  # a follower simply holds cash
            else:
                ret, lead, trades, reason = None, None, None, r.get("reason", "unavailable")
            s.execute(text("""
                INSERT INTO copy_score_outcome (wallet_id, as_of, horizon_days, since, until, ret, leader_ret, trades,
                  reason, computed_at) VALUES (:w, :d, :h, :s, :u, :r, :l, :n, :why, :now) ON CONFLICT DO NOTHING"""),
                {"w": wid, "d": as_of, "h": horizon, "s": graded, "u": graded + span, "r": ret, "l": lead, "n": trades,
                 "why": reason, "now": now})
            stored += 1
        s.commit()
    return stored


def ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    return (np.bincount(inv, r) / counts)[inv]  # ties share their mean rank


def spearman(a, b) -> float | None:
    ra, rb = ranks(np.asarray(a, float)), ranks(np.asarray(b, float))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def independent(days: list, horizon: int) -> list:
    chosen = []
    for d in days:
        if not chosen or (d - chosen[-1]).days >= horizon:
            chosen.append(d)
    return chosen


def evaluate(s: Session, horizon: int) -> dict:
    rows = s.execute(text("""
        SELECT h.as_of, h.score::float AS score, h.grade, o.ret::float AS ret, o.trades FROM copy_score_history h
        JOIN copy_score_outcome o ON o.wallet_id = h.wallet_id AND o.as_of = h.as_of AND o.horizon_days = :h
        WHERE h.status = 'scored' AND o.ret IS NOT NULL ORDER BY h.as_of"""), {"h": horizon}).mappings().all()
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r["as_of"], []).append(r)
    eligible = [d for d in sorted(by_day) if len(by_day[d]) >= MIN_TRADERS]
    days = independent(eligible, horizon)
    tested = []
    for d in days:
        group = by_day[d]
        rho = spearman([r["score"] for r in group], [r["ret"] for r in group])
        high = [r["ret"] for r in group if r["grade"] in HIGH]
        low = [r["ret"] for r in group if r["grade"] in LOW]
        if rho is not None:
            tested.append({"as_of": d, "n": len(group), "rho": rho,
                           "spread": float(np.mean(high) - np.mean(low)) if high and low else None})
    grades = {}
    for g in ("A", "B", "C", "D"):
        rets = [r["ret"] for d in days for r in by_day[d] if r["grade"] == g]
        grades[g] = {"n": len(rets), "mean": float(np.mean(rets)) if rets else None,
                     "median": float(np.median(rets)) if rets else None,
                     "win": sum(x > 0 for x in rets) / len(rets) if rets else None}
    result = {"horizon": horizon, "graded_days": len(by_day), "eligible_days": len(eligible), "days": tested,
              "grades": grades, "need_days": MIN_DAYS, "min_traders": MIN_TRADERS,
              "mean_rho": float(np.mean([t["rho"] for t in tested])) if tested else None,
              "p_value": None, "status": "accumulating"}
    if len(tested) >= MIN_DAYS:
        # one-sided sign-flip test: under no skill each day's rank correlation is as likely negative as positive
        values = np.array([t["rho"] for t in tested])
        signs = np.random.default_rng(17).choice([-1, 1], size=(4096, len(values)))
        result["p_value"] = float((1 + np.count_nonzero(signs @ values >= values.sum())) / 4097)
        result["status"] = "tested"
    return result
