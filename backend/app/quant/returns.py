"""Flow-adjusted returns from Hyperliquid `portfolio` series (metric set v1).

Each portfolio series gives (ts, accountValue) and (ts, cumulative pnl). Hyperliquid's pnl excludes
deposits/withdrawals/transfers, so between two points:

    dPnL  = pnl[i] - pnl[i-1]
    flow  = (AV[i] - AV[i-1]) - dPnL           # external cash in (+) / out (-)
    r[i]  = dPnL / (AV[i-1] + 0.5 * flow)       # Modified Dietz, flows assumed mid-period

TWR = prod(1 + r) - 1, MDD = worst peak-to-trough of the TWR index. Periods whose capital base is
below MIN_CAPITAL_USD are skipped (no meaningful denominator) and counted in the notes. Resolution is
whatever the API returns (allTime is ~100 points, i.e. days apart), so every result carries its span,
point count and median step and is labelled coarse; intra-period drawdowns are invisible.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

METRIC_VERSION = "r1"
MIN_CAPITAL_USD = 10.0
MIN_VALID_PERIODS = 3
COARSE_STEP_HOURS = 24.0
# A period whose external flow exceeds this share of the account is poorly served by the mid-period
# flow assumption; many of them (or a wipeout) make the TWR unreliable.
LARGE_FLOW_SHARE = 0.5

# metric window -> (portfolio series used, lookback)
METRIC_WINDOWS: dict[str, tuple[str, timedelta | None]] = {
    "30d": ("month", timedelta(days=30)),
    "90d": ("allTime", timedelta(days=90)),
    "180d": ("allTime", timedelta(days=180)),
    "365d": ("allTime", timedelta(days=365)),
    "all": ("allTime", None),
}


@dataclass(frozen=True)
class Point:
    ts: datetime
    account_value: float
    pnl: float


@dataclass
class SeriesMetrics:
    covered: bool
    span_start: datetime | None
    span_end: datetime | None
    n_points: int
    n_valid_periods: int
    median_step_hours: float | None
    twr: float | None
    mdd: float | None
    pnl: float | None
    pnl_mdd_usd: float | None
    net_flows: float | None
    positive_period_ratio: float | None
    reliability: str = "low"  # high | medium | low
    notes: list[str] = field(default_factory=list)


def _select(points: list[Point], start: datetime | None, end: datetime) -> tuple[list[Point], bool]:
    """Points inside [start, end], anchored by the last point at/before start. covered = data reaches back to start."""
    pts = sorted({p.ts: p for p in points if p.ts <= end}.values(), key=lambda p: p.ts)
    if start is None or not pts:
        return pts, bool(pts)
    before = [p for p in pts if p.ts <= start]
    inside = [p for p in pts if p.ts > start]
    if before:
        return [before[-1], *inside], True
    return inside, False


def compute_series_metrics(points: list[Point], lookback: timedelta | None, now: datetime) -> SeriesMetrics:
    start = now - lookback if lookback else None
    pts, covered = _select(points, start, now)
    notes: list[str] = []
    if len(pts) < 2:
        return SeriesMetrics(False, pts[0].ts if pts else None, pts[0].ts if pts else None, len(pts), 0,
                             None, None, None, None, None, None, None, ["insufficient_points"])
    if not covered:
        notes.append("history_shorter_than_window")

    steps = [(b.ts - a.ts).total_seconds() / 3600 for a, b in zip(pts, pts[1:])]
    med_step = median(steps)
    if med_step > COARSE_STEP_HOURS:
        notes.append("coarse_resolution")

    index = peak = 1.0
    mdd = 0.0
    cum = pnl_peak = 0.0
    pnl_mdd = 0.0
    flows = 0.0
    valid = positive = nonzero = skipped = large_flows = 0
    wipeout = False
    for a, b in zip(pts, pts[1:]):
        d_pnl = b.pnl - a.pnl
        flow = (b.account_value - a.account_value) - d_pnl
        flows += flow
        cum += d_pnl
        pnl_peak = max(pnl_peak, cum)
        pnl_mdd = min(pnl_mdd, cum - pnl_peak)
        base = a.account_value + 0.5 * flow
        if abs(flow) > LARGE_FLOW_SHARE * max(a.account_value, b.account_value, MIN_CAPITAL_USD):
            large_flows += 1
        if base < MIN_CAPITAL_USD:
            skipped += 1
            continue
        r = d_pnl / base
        if r <= -1.0:
            wipeout = True
            r = -1.0
        valid += 1
        if abs(r) > 1e-12:
            nonzero += 1
            positive += r > 0
        index *= 1.0 + r
        peak = max(peak, index)
        mdd = min(mdd, index / peak - 1.0)
    if wipeout:
        notes.append("wipeout_period")
    if skipped:
        notes.append(f"skipped_low_capital_periods:{skipped}")
    if large_flows:
        notes.append(f"large_flow_periods:{large_flows}")

    enough = valid >= MIN_VALID_PERIODS
    if not enough:
        notes.append("too_few_periods")
    n_periods = len(pts) - 1
    if not enough or wipeout or large_flows > 0.25 * n_periods or skipped > 0.25 * n_periods:
        reliability = "low"
    elif med_step > COARSE_STEP_HOURS or large_flows or not covered:
        reliability = "medium"
    else:
        reliability = "high"
    return SeriesMetrics(
        covered=covered,
        span_start=pts[0].ts,
        span_end=pts[-1].ts,
        n_points=len(pts),
        n_valid_periods=valid,
        median_step_hours=round(med_step, 3),
        twr=index - 1.0 if enough else None,
        mdd=mdd if enough else None,
        pnl=cum,
        pnl_mdd_usd=pnl_mdd,
        net_flows=flows,
        positive_period_ratio=(positive / nonzero) if enough and nonzero else None,
        reliability=reliability,
        notes=notes,
    )


def window_return(points: list[Point], start: datetime, end: datetime, tolerance: timedelta) -> dict | None:
    """Flow-adjusted TWR between the points nearest to `start` and `end` (each within `tolerance`).

    Used for swing legs, whose boundaries rarely coincide with portfolio sample times. Returns None
    when either boundary has no point close enough or fewer than 2 periods lie in between.
    """
    near = [p for p in points if start - tolerance <= p.ts <= end + tolerance]
    if len(near) < 3:
        return None
    a = min(near, key=lambda p: abs(p.ts - start))
    b = min(near, key=lambda p: abs(p.ts - end))
    if abs(a.ts - start) > tolerance or abs(b.ts - end) > tolerance or a.ts >= b.ts:
        return None
    seg = sorted({p.ts: p for p in near if a.ts <= p.ts <= b.ts}.values(), key=lambda p: p.ts)
    index = 1.0
    flows = 0.0
    valid = 0
    for x, y in zip(seg, seg[1:]):
        d_pnl = y.pnl - x.pnl
        flow = (y.account_value - x.account_value) - d_pnl
        flows += flow
        base = x.account_value + 0.5 * flow
        if base < MIN_CAPITAL_USD:
            continue
        index *= 1.0 + max(d_pnl / base, -1.0)
        valid += 1
    if valid < 2:
        return None
    return {"twr": index - 1.0, "pnl": seg[-1].pnl - seg[0].pnl, "net_flows": flows,
            "anchor_start": a.ts, "anchor_end": b.ts, "n_points": len(seg)}
