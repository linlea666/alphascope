"""Swing research, stage 1: which wallets made money on both sides of the big BTC moves?

1. Legs: zigzag pivots on daily highs/lows (major 15%, minor 7%). Objective and versioned, so nobody
   hand-picks flattering windows. The last leg is provisional until price reverses by the threshold.
   Legs are a retrospective yardstick for past behaviour, never a trading signal.
2. Per wallet and major leg: flow-adjusted return from portfolio allTime points (ALL assets — the
   portfolio curve cannot tell which coin made the money; stage 2 attributes it with fills).
3. Profile: legs covered / profitable on each side, capture ratios, and luck_p — the probability of
   being profitable in at least this many legs if each leg were a coin flip at the peer base rate.
4. Candidates go to the fill archive queue before their older fills fall out of the API window.
"""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingest.market_ingest import daily_closes
from app.quant.quality import AGGREGATE_ACCOUNT, DEFAULT_RANKING_EXCLUSIONS
from app.quant.returns import Point, window_return

log = logging.getLogger(__name__)

SWING_VERSION = "z1"
LEVELS = {"major": 0.15, "minor": 0.07}
LEGS_SINCE = datetime(2025, 1, 1, tzinfo=UTC)
ANCHOR_TOLERANCE = timedelta(days=5)
MIN_CANDIDATE_AV = 10_000
MIN_CYCLE_RETURN = 0.05
MAX_LUCK_P = 0.05
NOISE = (*DEFAULT_RANKING_EXCLUSIONS, AGGREGATE_ACCOUNT)


@dataclass(frozen=True)
class Bar:
    t: datetime
    h: float
    l: float  # noqa: E741


@dataclass(frozen=True)
class Leg:
    direction: str
    start_ts: datetime
    start_px: float
    end_ts: datetime
    end_px: float
    confirmed: bool

    @property
    def change(self) -> float:
        return self.end_px / self.start_px - 1


def zigzag(bars: list[Bar], threshold: float) -> list[Leg]:
    """Legs between alternating swing highs/lows that differ by at least `threshold`.

    A pivot is confirmed only once price has moved `threshold` away from it; the final leg runs to
    the most extreme bar since the last pivot and is marked unconfirmed.
    """
    if len(bars) < 2:
        return []
    pivots: list[tuple[int, str]] = []  # (bar index, "H" | "L")
    hi = lo = 0
    trend: str | None = None
    for i in range(1, len(bars)):
        b = bars[i]
        if trend is None:
            if b.h > bars[hi].h:
                hi = i
            if b.l < bars[lo].l:
                lo = i
            if hi < i and b.l <= bars[hi].h * (1 - threshold):
                pivots.append((hi, "H"))
                trend, lo = "down", min(range(hi + 1, i + 1), key=lambda k: bars[k].l)
            elif lo < i and b.h >= bars[lo].l * (1 + threshold):
                pivots.append((lo, "L"))
                trend, hi = "up", max(range(lo + 1, i + 1), key=lambda k: bars[k].h)
        elif trend == "up":
            if b.h > bars[hi].h:
                hi = i
            elif b.l <= bars[hi].h * (1 - threshold):
                pivots.append((hi, "H"))
                trend, lo = "down", min(range(hi + 1, i + 1), key=lambda k: bars[k].l)
        else:
            if b.l < bars[lo].l:
                lo = i
            elif b.h >= bars[lo].l * (1 + threshold):
                pivots.append((lo, "L"))
                trend, hi = "up", max(range(lo + 1, i + 1), key=lambda k: bars[k].h)

    def px(idx: int, kind: str) -> float:
        return bars[idx].h if kind == "H" else bars[idx].l

    legs = [
        Leg("down" if k0 == "H" else "up", bars[i0].t, px(i0, k0), bars[i1].t, px(i1, k1), True)
        for (i0, k0), (i1, k1) in zip(pivots, pivots[1:])
    ]
    if pivots:
        i0, k0 = pivots[-1]
        tail, kind = (lo, "L") if trend == "down" else (hi, "H")
        if tail > i0:
            legs.append(Leg("down" if k0 == "H" else "up", bars[i0].t, px(i0, k0), bars[tail].t,
                            px(tail, kind), False))
    return legs


def poisson_binomial_tail(ps: list[float], k: int) -> float:
    """P(X >= k) where X is the number of successes of independent Bernoulli(p_i)."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for j, q in enumerate(dist):
            nxt[j] += q * (1 - p)
            nxt[j + 1] += q * p
        dist = nxt
    return sum(dist[k:])


def compute_legs(session: Session, coin: str, now: datetime) -> dict[str, int]:
    bars = [Bar(t, h, l) for t, h, l, _ in daily_closes(session, coin, LEGS_SINCE)]
    counts = {}
    for level, threshold in LEVELS.items():
        legs = zigzag(bars, threshold)
        keep = [leg.start_ts for leg in legs]
        # Confirmed legs never change; provisional legs are replaced when the tail moves.
        session.execute(text("""
            DELETE FROM swing_leg WHERE coin = :coin AND version = :v AND level = :lvl
              AND (NOT confirmed OR NOT (start_ts = ANY(:keep)))"""),
            {"coin": coin, "v": SWING_VERSION, "lvl": level, "keep": keep})
        for leg in legs:
            session.execute(text("""
                INSERT INTO swing_leg (coin, version, level, direction, start_ts, start_px, end_ts, end_px,
                                       change, confirmed, computed_at)
                VALUES (:coin, :v, :lvl, :dir, :st, :sp, :et, :ep, :chg, :conf, :now)
                ON CONFLICT (coin, version, level, start_ts) DO UPDATE SET end_ts = EXCLUDED.end_ts,
                  end_px = EXCLUDED.end_px, change = EXCLUDED.change, confirmed = EXCLUDED.confirmed,
                  computed_at = EXCLUDED.computed_at"""),
                {"coin": coin, "v": SWING_VERSION, "lvl": level, "dir": leg.direction, "st": leg.start_ts,
                 "sp": leg.start_px, "et": leg.end_ts, "ep": leg.end_px, "chg": leg.change,
                 "conf": leg.confirmed, "now": now})
        counts[level] = len(legs)
    return counts


def _price_at(closes: list[tuple[datetime, float]], ts: datetime) -> float | None:
    """Close of the last daily candle that started at or before ts."""
    lo, hi = 0, len(closes) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if closes[mid][0] <= ts:
            best, lo = closes[mid][1], mid + 1
        else:
            hi = mid - 1
    return best


def screen_wallets(session: Session, settings: Settings, now: datetime) -> dict:
    coin = settings.swing_coin
    legs = session.execute(text("""
        SELECT id, direction, start_ts, end_ts, confirmed FROM swing_leg
        WHERE coin = :coin AND version = :v AND level = 'major' ORDER BY start_ts"""),
        {"coin": coin, "v": SWING_VERSION}).all()
    if not legs:
        return {"legs": 0}
    closes = [(t, c) for t, _, _, c in daily_closes(session, coin)]
    rows = session.execute(text("""
        SELECT e.wallet_id, e.ts, e.account_value, e.pnl FROM equity_point e
        WHERE e.series = 'allTime' ORDER BY e.wallet_id, e.ts""")).all()
    points: dict[int, list[Point]] = {}
    for wid, ts, av, pnl in rows:
        points.setdefault(wid, []).append(Point(ts, float(av), float(pnl)))

    leg_rows = []
    returns: dict[int, dict[int, float]] = {}
    for wid, pts in points.items():
        for leg in legs:
            r = window_return(pts, leg.start_ts, leg.end_ts, ANCHOR_TOLERANCE)
            if r is None:
                continue
            p0, p1 = _price_at(closes, r["anchor_start"]), _price_at(closes, r["anchor_end"])
            bench = (p1 / p0 - 1) if p0 and p1 else None
            leg_rows.append({"w": wid, "leg": leg.id, "now": now, **r, "bench": bench})
            returns.setdefault(wid, {})[leg.id] = r["twr"]
    session.execute(text("DELETE FROM wallet_leg_return WHERE leg_id = ANY(:ids)"), {"ids": [l.id for l in legs]})
    if leg_rows:
        session.execute(text("""
            INSERT INTO wallet_leg_return (wallet_id, leg_id, computed_at, anchor_start, anchor_end, n_points,
                                           twr, pnl, net_flows, bench_return)
            VALUES (:w, :leg, :now, :anchor_start, :anchor_end, :n_points, :twr, :pnl, :net_flows, :bench)"""),
            leg_rows)

    meta = {r.id: r for r in session.execute(text("""
        SELECT id, account_value, quality_flags || enrichment_flags AS flags FROM wallet
        WHERE id = ANY(:ids)"""), {"ids": list(returns)}).all()}
    clean = {w for w, m in meta.items()
             if m.account_value and float(m.account_value) >= 1000 and not set(m.flags or []) & set(NOISE)}
    # Peer base rate per leg: share of clean wallets covering the leg that were profitable in it.
    base: dict[int, float] = {}
    for leg in legs:
        covered = [returns[w][leg.id] for w in clean if leg.id in returns[w]]
        base[leg.id] = (sum(r > 0 for r in covered) / len(covered)) if covered else 0.5
    benches = {r["leg"]: [] for r in leg_rows}
    for r in leg_rows:
        if r["bench"] is not None:
            benches[r["leg"]].append(r["bench"])
    leg_bench = {lid: (sorted(v)[len(v) // 2] if v else None) for lid, v in benches.items()}
    last_up = next((l.id for l in reversed(legs) if l.direction == "up"), None)
    last_down = next((l.id for l in reversed(legs) if l.direction == "down"), None)
    direction = {l.id: l.direction for l in legs}

    profiles = []
    for wid, rs in returns.items():
        ups = [r for lid, r in rs.items() if direction[lid] == "up"]
        downs = [r for lid, r in rs.items() if direction[lid] == "down"]
        up_cap = [r / leg_bench[lid] for lid, r in rs.items()
                  if direction[lid] == "up" and leg_bench.get(lid)]
        down_cap = [r / abs(leg_bench[lid]) for lid, r in rs.items()
                    if direction[lid] == "down" and leg_bench.get(lid)]
        k = sum(r > 0 for r in rs.values())
        luck = poisson_binomial_tail([base[lid] for lid in rs], k) if k else 1.0
        cycle = [rs[i] for i in (last_up, last_down) if i in rs]
        both = len(cycle) == 2 and all(r > 0 for r in cycle)
        min_cycle = min(cycle) if len(cycle) == 2 else None
        m = meta.get(wid)
        is_clean = wid in clean and float(m.account_value) >= MIN_CANDIDATE_AV
        candidate = is_clean and (
            (both and min_cycle is not None and min_cycle > MIN_CYCLE_RETURN)
            or (len(rs) >= 3 and luck < MAX_LUCK_P and any(r > 0 for r in ups) and any(r > 0 for r in downs)))
        profiles.append({
            "w": wid, "v": SWING_VERSION, "now": now, "covered": len(rs), "profitable": k,
            "up": sum(r > 0 for r in ups), "down": sum(r > 0 for r in downs),
            "upc": sum(up_cap) / len(up_cap) if up_cap else None,
            "downc": sum(down_cap) / len(down_cap) if down_cap else None,
            "luck": luck, "both": both, "minc": min_cycle, "cand": candidate,
        })
    session.execute(text("DELETE FROM wallet_swing_profile WHERE version = :v"), {"v": SWING_VERSION})
    if profiles:
        session.execute(text("""
            INSERT INTO wallet_swing_profile (wallet_id, version, computed_at, legs_covered, legs_profitable,
              up_profitable, down_profitable, up_capture, down_capture, luck_p, current_cycle_both,
              min_cycle_return, candidate)
            VALUES (:w, :v, :now, :covered, :profitable, :up, :down, :upc, :downc, :luck, :both, :minc, :cand)"""),
            profiles)

    # Highest evidence first: profitable on both sides of the current cycle, then least likely luck.
    ranked = sorted((p for p in profiles if p["cand"]),
                    key=lambda p: (not p["both"], p["luck"], -(p["minc"] or 0)))[: settings.swing_max_candidates]
    start = datetime.fromisoformat(settings.swing_fill_start).replace(tzinfo=UTC)
    for rank, p in enumerate(ranked):
        session.execute(text("""
            INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at)
            VALUES (:w, 'swing_candidate', :prio, :start, 'queued', 0, :now)
            ON CONFLICT (wallet_id) DO UPDATE SET priority = greatest(fill_sync.priority, EXCLUDED.priority),
              requested_start = least(fill_sync.requested_start, EXCLUDED.requested_start)"""),
            {"w": p["w"], "prio": len(ranked) - rank, "start": start, "now": now})
    scanned = enqueue_macro_scan(session, settings, start, now)
    return {
        "legs": len(legs),
        "macro_scan_queued": scanned,
        "wallets_with_points": len(points),
        "wallets_screened": len(returns),
        "leg_rows": len(leg_rows),
        "base_rates": {str(k): round(v, 3) for k, v in base.items()},
        "candidates": sum(p["cand"] for p in profiles),
        "queued_for_fills": len(ranked),
        "p_profitable_in_every_leg": math.prod(base.values()) if base else None,
    }


def enqueue_macro_scan(session: Session, settings: Settings, start: datetime, now: datetime) -> int:
    """Queue clean, active tier-B wallets for fill archiving, lowest turnover first, below swing candidates."""
    rows = session.execute(text("""
        SELECT id FROM wallet
        WHERE present_in_latest AND tier = 'B' AND account_value >= :min_av AND month_vlm > 0
          AND NOT (quality_flags && CAST(:noise AS text[]) OR enrichment_flags && CAST(:noise AS text[]))
        ORDER BY month_vlm / account_value ASC
        LIMIT :n"""), {"min_av": MIN_CANDIDATE_AV, "noise": list(NOISE), "n": settings.macro_scan_max}).scalars().all()
    for rank, wid in enumerate(rows):
        session.execute(text("""
            INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at)
            VALUES (:w, 'macro_scan', :prio, :start, 'queued', 0, :now)
            ON CONFLICT (wallet_id) DO NOTHING"""), {"w": wid, "prio": -(rank + 1), "start": start, "now": now})
    return len(rows)


def run_swings(sf, settings: Settings) -> dict:
    now = datetime.now(UTC)
    with sf() as s:
        legs = compute_legs(s, settings.swing_coin, now)
        result = screen_wallets(s, settings, now)
        s.commit()
    return {"legs_by_level": legs, **result}
