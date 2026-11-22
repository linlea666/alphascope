"""Does the copyability grade predict what copying earns next? Grades are frozen once per UTC day
(copy_score_history). After a horizon has passed, every trader graded that day is copied over the days that followed,
with the nightly default settings. Traders are ranked against others graded the same day, so a market-wide move
cancels out, and only grading days at least one horizon apart enter the test, so no two tested windows overlap."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.factors.copyability import SCORE_VERSION
from app.quant.rank import spearman
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
        result.update(p_value=sign_flip([t["rho"] for t in tested]), status="tested")
    return result


def sign_flip(values) -> float:
    """One-sided: under no relation each independent day's rank correlation is as likely negative as positive."""
    values = np.asarray(values, float)
    signs = np.random.default_rng(17).choice([-1, 1], size=(4096, len(values)))
    return float((1 + np.count_nonzero(signs @ values >= values.sum())) / 4097)


PERSIST_LOOKBACK = 30
PERSIST_SAMPLE = 300
PERSIST_DATES = 40  # most recent dates per run; OKX history reaches back years
MIN_ACTIVE = 5  # decisions in the lookback window


def persistence(s: Session, venue: str, horizon: int, now: datetime, lookback: int = PERSIST_LOOKBACK,
                sample: int = PERSIST_SAMPLE) -> dict:
    """Did copying the traders with the best past copy return pay afterwards? On dates `horizon` days apart, a
    random sample of the venue's active traders is copied over the `lookback` days before the date and the
    `horizon` days after it, both with the nightly defaults, and the two returns are rank-correlated.

    Only data timestamped before each date enters the past return. The traders are those the archive knows today,
    so traders that stopped being tracked are missing (survivorship); the within-date ranking is less exposed to
    that than average returns are."""
    first = s.scalar(text("""SELECT min(d.ended_at) FROM trade_decision d JOIN wallet w ON w.id = d.wallet_id
        WHERE w.venue = :v"""), {"v": venue})
    # OKX copies are sized against the lead's current equity, the same denominator in both windows. Profitable traders
    # then look persistent partly through that shared scale, so OKX results overstate persistence.
    out = {"venue": venue, "horizon": horizon, "lookback": lookback, "sample": sample, "dates": [],
           "shared_denominator": venue == "okx",
           "need_days": MIN_DAYS, "min_traders": MIN_TRADERS, "mean_rho": None, "p_value": None,
           "status": "accumulating"}
    if first is None:
        return out
    day = (first + timedelta(days=lookback + 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    last = now - timedelta(days=horizon)
    if last - day > timedelta(days=horizon * (PERSIST_DATES - 1)):
        day += (last - day) // timedelta(days=horizon) * timedelta(days=horizon) - timedelta(days=horizon * (PERSIST_DATES - 1))
    while day + timedelta(days=horizon) <= now:
        wids = s.scalars(text("""SELECT d.wallet_id FROM trade_decision d JOIN wallet w ON w.id = d.wallet_id
            WHERE w.venue = :v AND d.ended_at >= :a AND d.ended_at < :b GROUP BY d.wallet_id HAVING count(*) >= :k
            ORDER BY md5(d.wallet_id::text || CAST(:b AS text)) LIMIT :n"""),
            {"v": venue, "a": day - timedelta(days=lookback), "b": day, "k": MIN_ACTIVE, "n": sample}).all()
        pairs = []
        if wids:
            prices = PriceBook(s, day - timedelta(days=lookback), day + timedelta(days=horizon))
            for wid in wids:
                past = backtest(s, wid, BacktestParams(days=lookback), now=day, prices=prices)
                if not past.get("ok") or not past.get("trades"):
                    continue
                after = backtest(s, wid, BacktestParams(days=horizon), now=day + timedelta(days=horizon),
                                 prices=prices)
                ret = after["ret"] if after.get("ok") else 0.0 if after.get("reason") == "no_decisions" else None
                if ret is not None:
                    pairs.append((past["ret"], ret))
        entry = {"as_of": day.date().isoformat(), "n": len(pairs), "rho": None, "top": None, "bottom": None}
        if len(pairs) >= MIN_TRADERS:
            pairs.sort()
            fifth = len(pairs) // 5
            entry.update(rho=spearman([p for p, _ in pairs], [f for _, f in pairs]),
                         bottom=float(np.mean([f for _, f in pairs[:fifth]])),
                         top=float(np.mean([f for _, f in pairs[-fifth:]])))
        out["dates"].append(entry)
        day += timedelta(days=horizon)
    tested = [d["rho"] for d in out["dates"] if d["rho"] is not None]
    if tested:
        out["mean_rho"] = float(np.mean(tested))
        spreads = [d["top"] - d["bottom"] for d in out["dates"] if d["rho"] is not None]
        out["top_minus_bottom"] = float(np.mean(spreads))
        out["positive_share"] = sum(r > 0 for r in tested) / len(tested)
    if len(tested) >= MIN_DAYS:
        out.update(p_value=sign_flip(tested), status="tested")
    return out


def refresh_persistence(sf, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        runs = [persistence(s, venue, h, now) for venue in ("hyperliquid", "okx") for h in HORIZONS]
    with sf.begin() as s:
        s.execute(text("INSERT INTO copy_persistence_run (result) VALUES (CAST(:r AS jsonb))"),
                  {"r": json.dumps({"computed_at": now.isoformat(), "runs": runs})})
    return {"status": "success", "dates": sum(len(r["dates"]) for r in runs)}
