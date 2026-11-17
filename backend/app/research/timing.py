"""Multi-timeframe timing scorecard (T3, version t1), see docs/09.

Every trading decision is scored at the timeframe scale of the position cycle it belongs to:

- location: where the decision price sits inside the price range of a window centred on it at that
  scale. Buy: (max - p) / (max - min); sell: (p - min) / (max - min). 1 = bought the low / sold the
  high of the window. Uses prices after the decision, so it grades past behaviour only.
- significance: the wallet's notional-weighted mean location vs. N_PERM circular time shifts of all its
  decisions (spacing and sides kept), p = share of shifts doing at least as well; Benjamini-Hochberg
  q-values across wallets per (scale, window, group).
- pivot precision / recall: decisions within tolerance of the scale's zigzag tops (sells) / bottoms (buys).
- alignment: share of position time whose direction matched the scale's current swing leg.

Candles: Binance USDT-M where imported (validated mapping), then Hyperliquid's own, then OKX's.
OKX traders (whole positions, open average price includes later adds) are not scored at the scalp scale.
"""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.research.holders import compute_wallet_captures
from app.research.swings import Bar, Leg, zigzag

log = logging.getLogger(__name__)

TIMING_VERSION = "t1"
# scale: (candle interval, minutes per bar, half window in bars, BTC zigzag threshold)
LADDER = {
    "scalp": ("15m", 15, 16, 0.015),   # window ±4h
    "short": ("1h", 60, 48, 0.04),     # ±2 days
    "swing": ("4h", 240, 42, 0.08),    # ±7 days
    "macro": ("1d", 1440, 30, 0.15),   # ±30 days
}
WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "all": None}
VALID_WINDOWS = {
    "scalp": ("7d", "30d", "90d"),
    "short": ("7d", "30d", "90d", "all"),
    "swing": ("30d", "90d", "all"),
    "macro": ("90d", "all"),
}
MIN_DECISIONS = {"scalp": 30, "short": 15, "swing": 6, "macro": 3}
GROUPS = {"all": None, "btc_eth": ("BTC", "ETH")}
# Per-coin groups ("c:<coin>") are added for coins with at least the scale's minimum scored decisions.
EXTREME = 0.8  # a decision in the best 20% of its window (buy near the low / sell near the high)
MAKER_HEAVY = 0.8  # share of resting (non-crossing) fills above which a wallet looks like a market maker / grid
N_PERM = 500
PIVOT_TIME_TOL = 0.2   # share of the leg's duration
PIVOT_PRICE_TOL = 0.25  # share of the leg's amplitude
PIVOTS_SINCE = datetime(2025, 1, 1, tzinfo=UTC)
VOL_CLAMP = (0.5, 4.0)


def _minutes(ts: datetime) -> int:
    return int(ts.timestamp() // 60)


@dataclass
class Series:
    """Candles on a regular grid (NaN for missing bars) with centred rolling window max/min."""

    t0: int  # epoch minutes of bar 0
    step: int
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    wmax: np.ndarray
    wmin: np.ndarray

    def index(self, minutes: np.ndarray) -> np.ndarray:
        return (minutes - self.t0) // self.step

    @property
    def last_index(self) -> int:
        return len(self.c) - 1


def load_series(session: Session, coin: str, interval: str, step: int, half: int) -> Series | None:
    rows = session.execute(text("""
        SELECT source, t, h, l, c FROM market_candle
        WHERE coin = :coin AND interval = :iv AND source IN ('binance_um', 'hyperliquid', 'okx')
        ORDER BY t, CASE source WHEN 'binance_um' THEN 0 WHEN 'hyperliquid' THEN 1 ELSE 2 END"""),
        {"coin": coin, "iv": interval}).all()
    bars: dict[int, tuple[float, float, float]] = {}
    for _source, t, h, lo, c in rows:  # first source per bar wins: Binance (validated archive), HL, OKX
        m = _minutes(t)
        if m not in bars:
            bars[m] = (float(h), float(lo), float(c))
    if len(bars) < 2 * half + 1:
        return None
    keys = sorted(bars)
    t0 = keys[0]
    n = (keys[-1] - t0) // step + 1
    h = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    c = np.full(n, np.nan)
    for m in keys:
        i = (m - t0) // step
        h[i], lo[i], c[i] = bars[m]
    w = 2 * half + 1
    wmax = np.full(n, np.nan)
    wmin = np.full(n, np.nan)
    if n >= w:
        wmax[half:n - half] = np.lib.stride_tricks.sliding_window_view(h, w).max(axis=1)
        wmin[half:n - half] = np.lib.stride_tricks.sliding_window_view(lo, w).min(axis=1)
    return Series(t0, step, h, lo, c, wmax, wmin)


def location(side_buy: np.ndarray, price: np.ndarray, wmax: np.ndarray, wmin: np.ndarray) -> np.ndarray:
    rng = wmax - wmin
    with np.errstate(invalid="ignore", divide="ignore"):
        loc = np.where(side_buy, (wmax - price) / rng, (price - wmin) / rng)
    loc = np.where(rng > 0, loc, np.nan)
    return np.clip(loc, 0.0, 1.0)


def vol_ratio(session: Session, coin: str) -> float:
    """90-day daily volatility of `coin` relative to BTC, to scale zigzag thresholds per coin."""

    def vol(c: str) -> float | None:
        closes = [float(r[0]) for r in session.execute(text("""
            SELECT DISTINCT ON (t) c FROM market_candle WHERE coin = :c AND interval = '1d'
              AND source IN ('binance_um', 'hyperliquid', 'okx') AND t >= now() - interval '90 days'
            ORDER BY t, CASE source WHEN 'binance_um' THEN 0 WHEN 'hyperliquid' THEN 1 ELSE 2 END"""), {"c": c}).all()]
        if len(closes) < 30:
            return None
        r = np.diff(np.log(np.array(closes)))
        return float(np.std(r))

    base, own = vol("BTC"), vol(coin)
    if not base or not own:
        return 1.0
    return min(max(own / base, VOL_CLAMP[0]), VOL_CLAMP[1])


@dataclass(frozen=True)
class Pivot:
    kind: str  # H | L
    minute: int
    px: float
    amplitude: float  # price distance covered by the leg ending here
    leg_minutes: float
    confirmed: bool


def pivots_from_legs(legs: list[Leg]) -> list[Pivot]:
    return [Pivot("H" if leg.direction == "up" else "L", _minutes(leg.end_ts), leg.end_px,
                  abs(leg.end_px - leg.start_px), (leg.end_ts - leg.start_ts).total_seconds() / 60, leg.confirmed)
            for leg in legs]


def build_market(session: Session, coins: list[str]) -> dict[tuple[str, str], tuple[Series, list[Leg], list[Pivot]]]:
    """Series, legs and pivots per (coin, scale); persisted pivots of these coins are replaced for this version."""
    out = {}
    session.execute(text("DELETE FROM timing_pivot WHERE version = :v AND coin = ANY(:coins)"),
                    {"v": TIMING_VERSION, "coins": list(coins)})
    for coin in coins:
        ratio = None
        for scale, (interval, step, half, base_thr) in LADDER.items():
            s = load_series(session, coin, interval, step, half)
            if s is None:
                continue
            ratio = ratio if ratio is not None else vol_ratio(session, coin)
            since = _minutes(PIVOTS_SINCE)
            bars = [Bar(datetime.fromtimestamp((s.t0 + i * step) * 60, tz=UTC), float(s.h[i]), float(s.l[i]))
                    for i in range(len(s.c)) if not math.isnan(s.h[i]) and s.t0 + i * step >= since]
            legs = zigzag(bars, base_thr * ratio)
            pivots = pivots_from_legs(legs)
            if pivots:
                session.execute(text("""
                    INSERT INTO timing_pivot (coin, scale, version, kind, ts, px, leg_change, leg_hours, confirmed)
                    VALUES (:coin, :scale, :v, :kind, :ts, :px, :chg, :hours, :conf)"""),
                    [{"coin": coin, "scale": scale, "v": TIMING_VERSION, "kind": p.kind,
                      "ts": datetime.fromtimestamp(p.minute * 60, tz=UTC), "px": p.px,
                      "chg": (leg.end_px / leg.start_px - 1), "hours": p.leg_minutes / 60, "conf": p.confirmed}
                     for p, leg in zip(pivots, legs)])
            out[(coin, scale)] = (s, legs, pivots)
    return out


def match_pivot(pivots: list[Pivot], minute: int, price: float, buy: bool, step: int) -> Pivot | None:
    best = None
    for p in pivots:
        if p.kind != ("L" if buy else "H"):
            continue
        tol_t = max(PIVOT_TIME_TOL * p.leg_minutes, step)
        if abs(minute - p.minute) > tol_t:
            continue
        ok = price <= p.px + PIVOT_PRICE_TOL * p.amplitude if buy else price >= p.px - PIVOT_PRICE_TOL * p.amplitude
        if ok and (best is None or abs(minute - p.minute) < abs(minute - best.minute)):
            best = p
    return best


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    q = [0.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * n / rank)
        q[i] = running
    return q


@dataclass
class Scored:
    decision_id: int
    coin: str
    scale: str
    minute: int
    buy: bool
    weight: float
    loc: float | None
    status: str
    pivot: Pivot | None


def permutation_test(items: list[Scored], market, span: tuple[int, int], rng: np.random.Generator) -> tuple[float, float, float]:
    """(null mean, p, null extreme share) for the weighted mean location under circular time shifts."""
    a, b = span
    length = max(b - a, 1)
    shifts = rng.integers(0, length, size=N_PERM)[:, None]
    num = np.zeros(N_PERM)
    den = np.zeros(N_PERM)
    ext_num = np.zeros(N_PERM)
    ext_den = np.zeros(N_PERM)
    actual_w = sum(i.weight for i in items)
    actual = sum(i.weight * i.loc for i in items) / actual_w
    by_coin: dict[str, list[Scored]] = {}
    for it in items:
        by_coin.setdefault(it.coin, []).append(it)
    for coin, group in by_coin.items():
        s = market[(coin, group[0].scale)][0]
        minutes = np.array([g.minute for g in group])
        buy = np.array([g.buy for g in group])
        w = np.array([g.weight for g in group])
        shifted = a + (minutes[None, :] - a + shifts) % length
        idx = s.index(shifted)
        valid = (idx >= 0) & (idx <= s.last_index)
        idx = np.where(valid, idx, 0)
        loc = location(np.broadcast_to(buy, idx.shape), s.c[idx], s.wmax[idx], s.wmin[idx])
        ok = valid & ~np.isnan(loc)
        num += np.where(ok, loc * w, 0).sum(axis=1)
        den += np.where(ok, w, 0).sum(axis=1)
        ext_num += (ok & (loc >= EXTREME)).sum(axis=1)
        ext_den += ok.sum(axis=1)
    null = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    null = null[~np.isnan(null)]
    if null.size == 0:
        return float("nan"), float("nan"), float("nan")
    p = (1 + int((null >= actual - 1e-12).sum())) / (1 + null.size)
    extreme = float(ext_num.sum() / ext_den.sum()) if ext_den.sum() else float("nan")
    return float(null.mean()), p, extreme


COARSE_SCALES = {"okx": {"scalp"}}  # venues reporting whole positions: too coarse for the 15m window


def score_wallet(session: Session, wallet_id: int, as_of: datetime, market, rng: np.random.Generator,
                 skip_scales: set[str] = frozenset()) -> list[dict]:
    rows = session.execute(text("""
        SELECT d.id, d.coin, d.side, d.started_at, d.vwap, d.notional, c.scale
        FROM trade_decision d JOIN position_cycle c ON c.id = d.cycle_id
        WHERE d.wallet_id = :w ORDER BY d.started_at"""), {"w": wallet_id}).all()
    scored: list[Scored] = []
    for did, coin, side, started, vwap, notional, scale in rows:
        minute = _minutes(started)
        buy = side == "buy"
        if scale in skip_scales:
            scored.append(Scored(did, coin, scale, minute, buy, float(notional), None, "coarse", None))
            continue
        entry = market.get((coin, scale))
        if entry is None:
            scored.append(Scored(did, coin, scale, minute, buy, float(notional), None, "no_data", None))
            continue
        s, _, pivots = entry
        i = int(s.index(np.array([minute]))[0])
        half = LADDER[scale][2]
        if i < 0 or i > s.last_index or i + half > s.last_index:
            status = "pending" if i + half > s.last_index and i >= 0 else "no_data"
            scored.append(Scored(did, coin, scale, minute, buy, float(notional), None, status, None))
            continue
        loc = float(location(np.array([buy]), np.array([float(vwap)]), s.wmax[i:i + 1], s.wmin[i:i + 1])[0])
        if math.isnan(loc):
            scored.append(Scored(did, coin, scale, minute, buy, float(notional), None, "no_data", None))
            continue
        piv = match_pivot(pivots, minute, float(vwap), buy, LADDER[scale][1])
        scored.append(Scored(did, coin, scale, minute, buy, float(notional), loc, "scored", piv))

    session.execute(text("DELETE FROM decision_timing WHERE wallet_id = :w"), {"w": wallet_id})
    if scored:
        session.execute(text("""
            INSERT INTO decision_timing (decision_id, wallet_id, coin, scale, version, t, side, status, location,
                                         weight, pivot_ts)
            VALUES (:d, :w, :coin, :scale, :v, :t, :side, :st, :loc, :wt, :pts)"""),
            [{"d": s.decision_id, "w": wallet_id, "coin": s.coin, "scale": s.scale, "v": TIMING_VERSION,
              "t": datetime.fromtimestamp(s.minute * 60, tz=UTC), "side": "buy" if s.buy else "sell",
              "st": s.status, "loc": s.loc, "wt": s.weight,
              "pts": datetime.fromtimestamp(s.pivot.minute * 60, tz=UTC) if s.pivot else None} for s in scored])

    fills = session.execute(text("""
        SELECT coin, time, crossed FROM fill WHERE wallet_id = :w AND crossed IS NOT NULL"""), {"w": wallet_id}).all()
    fill_minutes = [(c, _minutes(t), bool(x)) for c, t, x in fills]
    per_coin_counts: dict[tuple[str, str], int] = {}
    for s_ in scored:
        if s_.status == "scored":
            per_coin_counts[(s_.scale, s_.coin)] = per_coin_counts.get((s_.scale, s_.coin), 0) + 1
    cycles = session.execute(text("""
        SELECT coin, scale, direction, opened_at, coalesce(closed_at, :as_of) AS end_at, status,
               realized_pnl - fees AS net FROM position_cycle WHERE wallet_id = :w"""),
        {"w": wallet_id, "as_of": as_of}).all()
    now_min = _minutes(as_of)
    results = []
    for scale in LADDER:
        for window, days in WINDOWS.items():
            start_min = now_min - days * 1440 if days else None
            groups = dict(GROUPS)
            groups.update({f"c:{coin}": (coin,) for (sc, coin), n in per_coin_counts.items()
                           if sc == scale and n >= MIN_DECISIONS[scale]})
            for group, coins in groups.items():
                def in_scope(coin: str, minute: int) -> bool:
                    return (coins is None or coin in coins) and (start_min is None or minute >= start_min)

                items = [s for s in scored if s.scale == scale and in_scope(s.coin, s.minute)]
                ok = [s for s in items if s.status == "scored"]
                res = {"w": wallet_id, "scale": scale, "window": window, "group": group, "v": TIMING_VERSION,
                       "as_of": as_of, "n": len(items), "n_scored": len(ok), "loc": None, "null": None,
                       "edge": None, "edge_s": None, "p": None, "hits": sum(1 for s in ok if s.pivot),
                       "pivots": 0, "pivots_hit": 0, "precision": None, "recall": None, "align": None,
                       "cycles": 0, "net": None, "n_buy": 0, "n_sell": 0, "buy_loc": None, "sell_loc": None,
                       "buy_ext": None, "sell_ext": None, "null_ext": None, "maker": None}
                scoped_fills = [x for c, m, x in fill_minutes if in_scope(c, m)]
                if scoped_fills:
                    res["maker"] = sum(1 for x in scoped_fills if not x) / len(scoped_fills)
                if ok:
                    w = sum(s.weight for s in ok) or 1.0
                    res["loc"] = sum(s.weight * s.loc for s in ok) / w
                    for side, key in ((True, "buy"), (False, "sell")):
                        part = [s for s in ok if s.buy == side]
                        res[f"n_{key}"] = len(part)
                        if part:
                            pw = sum(s.weight for s in part) or 1.0
                            res[f"{key}_loc"] = sum(s.weight * s.loc for s in part) / pw
                            res[f"{key}_ext"] = sum(1 for s in part if s.loc >= EXTREME) / len(part)
                    res["precision"] = res["hits"] / len(ok)
                    # recall: pivots on traded coins inside the wallet's active span per coin
                    span_by_coin: dict[str, tuple[int, int]] = {}
                    for s in ok:
                        lo_, hi_ = span_by_coin.get(s.coin, (s.minute, s.minute))
                        span_by_coin[s.coin] = (min(lo_, s.minute), max(hi_, s.minute))
                    hit_set = {(s.coin, s.pivot.minute) for s in ok if s.pivot}
                    # pivots inside the active span, plus any matched just outside it (time tolerance)
                    opportunities = set(hit_set)
                    for coin, (a, b) in span_by_coin.items():
                        opportunities |= {(coin, p.minute) for p in market[(coin, scale)][2] if a <= p.minute <= b}
                    res["pivots"], res["pivots_hit"] = len(opportunities), len(hit_set)
                    res["recall"] = len(hit_set) / len(opportunities) if opportunities else None
                # alignment and realized result of cycles at this scale
                aligned = covered = 0.0
                net = 0.0
                closed = 0
                for coin, c_scale, direction, opened, end_at, status, cnet in cycles:
                    if c_scale != scale or (coins is not None and coin not in coins):
                        continue
                    o, e = _minutes(opened), _minutes(end_at)
                    if start_min is not None and e < start_min:
                        continue
                    if status != "open" and (start_min is None or e >= start_min):
                        closed += 1
                        net += float(cnet)
                    entry = market.get((coin, scale))
                    if entry is None:
                        continue
                    o = max(o, start_min) if start_min is not None else o
                    for leg in entry[1]:
                        ls, le = _minutes(leg.start_ts), _minutes(leg.end_ts)
                        ov = min(e, le) - max(o, ls)
                        if ov > 0:
                            covered += ov
                            if (leg.direction == "up") == (direction == "long"):
                                aligned += ov
                res["align"] = aligned / covered if covered else None
                res["cycles"], res["net"] = closed, net if closed else None
                if window not in VALID_WINDOWS[scale]:
                    res["status"] = "invalid_window"
                elif len(ok) < MIN_DECISIONS[scale]:
                    res["status"] = "insufficient"
                else:
                    res["status"] = "scored"
                    span = (start_min if start_min is not None else min(s.minute for s in ok), now_min)
                    null_mean, p, null_ext = permutation_test(ok, market, span, rng)
                    if not math.isnan(null_mean):
                        res["null"], res["p"], res["null_ext"] = null_mean, p, null_ext
                        res["edge"] = res["loc"] - null_mean
                        res["edge_s"] = res["edge"] * len(ok) / (len(ok) + MIN_DECISIONS[scale])
                results.append(res)
    return results


def run_timing(sf: sessionmaker[Session], seed: int = 7, venue: str = "hyperliquid") -> dict:
    """Recompute pivots, decision timing and timing scores for every reconstructed trader of one venue.

    Venues run separately (Hyperliquid every few hours, OKX daily: its history changes slowly and it is the larger
    population), so each venue is its own Benjamini-Hochberg family.
    """
    rng = np.random.default_rng(seed)
    with sf() as s:
        coins = s.execute(text("""
            SELECT DISTINCT d.coin FROM trade_decision d JOIN wallet w ON w.id = d.wallet_id
            WHERE w.venue = :venue"""), {"venue": venue}).scalars().all()
        market = build_market(s, coins)
        wallets = s.execute(text("""SELECT * FROM (
            SELECT f.wallet_id, CASE WHEN f.status = 'synced' THEN now() ELSE f.latest_fill_at END AS as_of,
                   CASE WHEN coalesce(f.truncated, false) THEN f.earliest_fill_at ELSE f.requested_start END AS coverage,
                   'hyperliquid' AS venue
            FROM fill_sync f WHERE f.reconstructed_at IS NOT NULL
            UNION ALL  -- OKX: positions are known up to the last history sync, from the oldest one we hold
            SELECT t.wallet_id, coalesce(t.history_synced_at, t.newest_at), t.oldest_at, 'okx'
            FROM okx_trader t WHERE t.derived_at IS NOT NULL AND t.n_positions > 0) x
            WHERE venue = :venue"""), {"venue": venue}).all()
        s.commit()
    all_rows: list[dict] = []
    captures = 0
    for wid, as_of, coverage, venue in wallets:
        with sf() as s:
            rows = score_wallet(s, wid, as_of, market, rng, COARSE_SCALES.get(venue, set()))
            captures += compute_wallet_captures(s, wid, as_of, coverage, market, net=venue == "okx")
            s.commit()
        all_rows.extend(rows)
    # Benjamini-Hochberg per (scale, window, group) across wallets
    buckets: dict[tuple, list[dict]] = {}
    for r in all_rows:
        if r["p"] is not None:
            buckets.setdefault((r["scale"], r["window"], r["group"]), []).append(r)
    for rows in buckets.values():
        for r, q in zip(rows, benjamini_hochberg([r["p"] for r in rows])):
            r["q"] = q
    with sf() as s:
        s.execute(text("""DELETE FROM timing_score t USING wallet w
                           WHERE w.id = t.wallet_id AND t.version = :v AND w.venue = :venue"""),
                  {"v": TIMING_VERSION, "venue": venue})
        if all_rows:
            s.execute(text("""
                INSERT INTO timing_score (wallet_id, scale, time_window, coin_group, version, as_of, status, n_decisions,
                  n_scored, location, null_mean, edge, edge_shrunk, p_value, q_value, pivot_hits, pivots, pivots_hit,
                  precision, recall, alignment, cycles, net_pnl, n_buy, n_sell, buy_location, sell_location,
                  buy_extreme, sell_extreme, null_extreme, maker_share)
                VALUES (:w, :scale, :window, :group, :v, :as_of, :status, :n, :n_scored, :loc, :null, :edge, :edge_s,
                  :p, :q, :hits, :pivots, :pivots_hit, :precision, :recall, :align, :cycles, :net, :n_buy, :n_sell,
                  :buy_loc, :sell_loc, :buy_ext, :sell_ext, :null_ext, :maker)"""),
                [{**r, "q": r.get("q")} for r in all_rows])
        s.commit()
    scored = [r for r in all_rows if r["status"] == "scored"]
    return {"venue": venue, "coins_with_series": len({c for c, _ in market}), "series": len(market),
            "wallets": len(wallets),
            "leg_captures": captures,
            "scores": len(all_rows), "scored": len(scored),
            "significant_q10": sum(1 for r in scored if r.get("q") is not None and r["q"] < 0.1)}
