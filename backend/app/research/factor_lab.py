"""Exploratory historical screen of Binance positioning factors for BTC and ETH.

Each UTC midnight d is a decision point. Factors use only what was published before it: the 5-minute metrics of
day d-1, funding settled before d and daily closes up to d-1. Outcomes are the Binance USDT-M close-to-close
returns over the next 1 and 3 days. The first two thirds of the days are in-sample and the rest out-of-sample, with
one horizon purged at the boundary. A factor's information coefficient (Spearman) is tested against circular shifts
of the factor series, which keep its own autocorrelation, and 3-day outcomes use every third day so windows never
overlap. Benjamini-Hochberg runs over every factor, coin and horizon at once. A candidate must also keep its sign out
of sample. This is a screen for hypotheses to pre-register, not evidence of a tradable edge."""

import json
import math
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import text

from app.quant.rank import ranks
from app.research.timing import benjamini_hochberg

VERSION = "lab-v1"
HORIZONS = (1, 3)
MIN_DAYS = 200
MIN_ROWS = 250  # of 288 five-minute stats in a day
ZWINDOW, ZMIN = 90, 60
MIN_SHIFT = 30  # days: shifts shorter than this would keep most of the alignment
FDR, OOS_ALPHA = 0.1, 0.1

FACTORS = {
    "oi_chg_1d": "持仓量 1 天变化（对数）",
    "oi_chg_7d": "持仓量 7 天变化（对数）",
    "oi_with_price": "持仓量 1 天变化 × 价格 1 天方向（顺势加仓为正）",
    "top_position_z": "头部交易员持仓多空比，相对过去 90 天的 z 值",
    "top_position_chg_1d": "头部交易员持仓多空比 1 天变化",
    "account_ratio_z": "全体账户多空人数比，相对过去 90 天的 z 值",
    "top_vs_crowd_z": "头部持仓多空比相对全体账户多空比，过去 90 天 z 值",
    "taker_1d": "前一天主动买入/卖出量比的对数均值",
    "funding_1d": "前 24 小时已结算资金费率之和",
    "funding_7d_z": "7 天平均资金费率，相对过去 90 天的 z 值",
    "mom_1d": "价格 1 天动量（对照）",
    "mom_7d": "价格 7 天动量（对照）",
}
REGIMES = {(1, 1): "long_build", (1, -1): "short_cover", (-1, 1): "short_build", (-1, -1): "long_unwind"}


def load(s, coin):
    days = s.execute(text("""
        SELECT (t AT TIME ZONE 'UTC')::date AS day, count(*) AS n,
          (array_agg(oi::float ORDER BY t DESC))[1] AS oi,
          (array_agg(top_position_ratio ORDER BY t DESC))[1] AS tpr,
          (array_agg(account_ratio ORDER BY t DESC))[1] AS ar,
          avg(ln(taker_ratio)) FILTER (WHERE taker_ratio > 0) AS taker
        FROM binance_metric WHERE coin = :c GROUP BY 1 ORDER BY 1"""), {"c": coin}).mappings().all()
    closes = dict(s.execute(text("""SELECT (t AT TIME ZONE 'UTC')::date, c::float FROM market_candle
        WHERE source = 'binance_um' AND coin = :c AND interval = '1d' AND c > 0"""), {"c": coin}).all())
    funding = []
    for (rows,) in s.execute(text("""SELECT settlements FROM funding_archive_month
            WHERE venue = 'binance' AND coin = :c ORDER BY month"""), {"c": coin}):
        funding += [(stamp, rate) for stamp, rate, _ in rows]
    return [dict(r) for r in days if r["n"] >= MIN_ROWS], closes, sorted(funding)


def zscore(series, i):
    window = [x for x in series[max(0, i - ZWINDOW):i] if x is not None]
    if series[i] is None or len(window) < ZMIN:
        return None
    sd = float(np.std(window))
    return (series[i] - float(np.mean(window))) / sd if sd > 0 else None


def log_ratio(a, b):
    return math.log(a / b) if a and b and a > 0 and b > 0 else None


def build(days, closes, funding):
    """One row per decision day d (= metrics day + 1) with factors and forward returns."""
    by_day = {r["day"]: r for r in days}
    order = sorted(by_day)
    tpr = [log_ratio(by_day[d]["tpr"], 1) for d in order]
    ar = [log_ratio(by_day[d]["ar"], 1) for d in order]
    gap = [a - b if a is not None and b is not None else None for a, b in zip(tpr, ar)]
    stamps = [f[0] for f in funding]
    f7 = []
    rows = []
    for i, day in enumerate(order):
        d = day + timedelta(days=1)
        cutoff = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)
        lo1, lo7, hi = (np.searchsorted(stamps, cutoff - k * 86_400_000) for k in (1, 7, 0))
        f7.append(float(np.mean([r for _, r in funding[lo7:hi]])) if hi - lo7 >= 14 else None)
        r = by_day[day]
        prior = lambda k: by_day.get(day - timedelta(days=k))  # noqa: E731
        close = lambda k: closes.get(d - timedelta(days=k))  # noqa: E731 - close of day d-k
        mom1 = log_ratio(close(1), close(2))
        oi1 = log_ratio(r["oi"], prior(1) and prior(1)["oi"])
        row = {
            "day": d,
            "oi_chg_1d": oi1,
            "oi_chg_7d": log_ratio(r["oi"], prior(7) and prior(7)["oi"]),
            "oi_with_price": oi1 * math.copysign(1, mom1) if oi1 is not None and mom1 else None,
            "top_position_z": zscore(tpr, i),
            "top_position_chg_1d": log_ratio(r["tpr"], prior(1) and prior(1)["tpr"]),
            "account_ratio_z": zscore(ar, i),
            "top_vs_crowd_z": zscore(gap, i),
            "taker_1d": float(r["taker"]) if r["taker"] is not None else None,
            "funding_1d": float(sum(x for _, x in funding[lo1:hi])) if hi - lo1 >= 1 else None,
            "funding_7d_z": zscore(f7, i),
            "mom_1d": mom1,
            "mom_7d": log_ratio(close(1), close(8)),
            "regime": REGIMES.get((int(math.copysign(1, mom1)), int(math.copysign(1, oi1))))
            if mom1 and oi1 else None,
        }
        for h in HORIZONS:
            end, start = closes.get(d + timedelta(days=h - 1)), close(1)
            row[f"fwd_{h}d"] = end / start - 1 if end and start else None
        rows.append(row)
    return rows


def shift_test(x, y, sign=0):
    """IC and its p-value against every circular shift of x of at least MIN_SHIFT steps (two-sided if sign=0,
    else one-sided in that direction)."""
    rx, ry = ranks(np.asarray(x, float)), ranks(np.asarray(y, float))
    if rx.std() == 0 or ry.std() == 0:
        return None, None
    rx, ry = (rx - rx.mean()) / rx.std(), (ry - ry.mean()) / ry.std()
    n = len(rx)
    ic = float(rx @ ry / n)
    null = np.array([np.roll(rx, k) @ ry / n for k in range(MIN_SHIFT, n - MIN_SHIFT + 1)])
    if not len(null):
        return ic, None
    hits = np.abs(null) >= abs(ic) if sign == 0 else sign * null >= sign * ic
    return ic, float((1 + np.count_nonzero(hits)) / (1 + len(null)))


def screen(rows, split):
    """[{factor, horizon, in-sample and out-of-sample IC with p}] before the multiple-testing correction."""
    out = []
    for h in HORIZONS:
        step = h  # non-overlapping outcome windows
        for key in FACTORS:
            usable = [r for r in rows[::step] if r[key] is not None and r[f"fwd_{h}d"] is not None]
            train = [r for r in usable if r["day"] < split]
            test = [r for r in usable if r["day"] >= split + timedelta(days=h)]
            item = {"factor": key, "horizon": h, "n_is": len(train), "n_oos": len(test), "ic_is": None,
                    "p_is": None, "ic_oos": None, "p_oos": None, "q": None, "status": "none"}
            if len(train) >= 2 * MIN_SHIFT + 20:
                item["ic_is"], item["p_is"] = shift_test([r[key] for r in train], [r[f"fwd_{h}d"] for r in train])
            if item["ic_is"] and len(test) >= 2 * MIN_SHIFT + 10:
                item["ic_oos"], item["p_oos"] = shift_test([r[key] for r in test], [r[f"fwd_{h}d"] for r in test],
                                                           sign=1 if item["ic_is"] > 0 else -1)
            out.append(item)
    return out


def regimes(rows):
    out = {}
    for name in REGIMES.values():
        group = [r for r in rows if r["regime"] == name]
        out[name] = {"n": len(group)}
        for h in HORIZONS:
            vals = [r[f"fwd_{h}d"] for r in group if r[f"fwd_{h}d"] is not None]
            out[name][f"mean_{h}d"] = float(np.mean(vals)) if vals else None
            out[name][f"up_{h}d"] = sum(v > 0 for v in vals) / len(vals) if vals else None
    for h in HORIZONS:
        vals = [r[f"fwd_{h}d"] for r in rows if r[f"fwd_{h}d"] is not None]
        out.setdefault("all", {"n": len(vals)})[f"mean_{h}d"] = float(np.mean(vals)) if vals else None
    return out


def run(s, coins, now=None):
    now = now or datetime.now(UTC)
    result = {"version": VERSION, "computed_at": now.isoformat(), "fdr": FDR, "oos_alpha": OOS_ALPHA,
              "factors": FACTORS, "coins": {}}
    tests = []
    for coin in coins:
        rows = [r for r in build(*load(s, coin)) if r["day"] <= now.date()]
        complete = [r for r in rows if r["fwd_1d"] is not None]
        entry = {"days": len(complete), "start": str(rows[0]["day"]) if rows else None,
                 "end": str(complete[-1]["day"]) if complete else None, "status": "insufficient_history"}
        if len(complete) >= MIN_DAYS:
            split = complete[len(complete) * 2 // 3]["day"]
            entry.update(status="screened", split=str(split), tests=screen(complete, split), regimes=regimes(complete))
            tests += [(coin, t) for t in entry["tests"] if t["p_is"] is not None]
        if rows:
            latest = rows[-1]
            entry["today"] = {"day": str(latest["day"]), "regime": latest["regime"], "values": {
                k: {"value": latest[k], "percentile": percentile(latest[k], [r[k] for r in rows[:-1]])}
                for k in FACTORS}}
        result["coins"][coin] = entry
    for (coin, t), q in zip(tests, benjamini_hochberg([t["p_is"] for _, t in tests])):
        t["q"] = q
        kept = t["ic_oos"] is not None and t["p_oos"] is not None and t["p_oos"] < OOS_ALPHA
        t["status"] = "candidate" if q < FDR and kept else "in_sample_only" if q < FDR else "none"
    result["tests"] = len(tests)
    return result


def percentile(value, history):
    xs = [x for x in history if x is not None]
    if value is None or not xs:
        return None
    return (sum(x < value for x in xs) + 0.5 * sum(x == value for x in xs)) / len(xs)


def refresh(sf, coins):
    with sf.begin() as s:
        result = run(s, coins)
        s.execute(text("INSERT INTO factor_lab_run (version, result) VALUES (:v, CAST(:r AS jsonb))"),
                  {"v": VERSION, "r": json.dumps(result, default=str)})
    return {"status": "success", "tests": result["tests"],
            "candidates": sum(t.get("status") == "candidate" for c in result["coins"].values() for t in c.get("tests", []))}
