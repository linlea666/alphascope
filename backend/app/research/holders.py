"""Macro swing holders (version c1): how much of each macro swing leg a wallet held and captured.

For every coin with macro-scale zigzag legs (timing.LADDER["macro"]), the wallet's position path from its
decisions is marked to market on hourly closes across the leg:

- time_aligned / time_against: share of leg hours positioned with / against the leg direction
- pnl_mark: sum of position x hourly price change over the leg (fees and funding ignored)
- capture_ratio: pnl_mark / (max notional during the leg x |leg move|); 1.0 = held a full position
  through the whole leg. Scaling in/out or rolling positions across several cycles is all counted.

Macro pivots come from daily candles (bar time 00:00 UTC); each leg's start and end are refined to the hour
of the extreme within that day using hourly candles. A leg is "partial" when the wallet's archived fills
start after the leg starts or stop before it ends (sync not finished yet).
"""

from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

CAPTURE_VERSION = "c1"
LEGS_SINCE = datetime(2026, 6, 1, tzinfo=UTC)


def position_path(decisions: list[tuple[datetime, float, float]], hours: np.ndarray) -> np.ndarray:
    """Position at the start of each hour. decisions: (ended_at, pos_before, pos_after), time-ordered."""
    if not decisions:
        return np.zeros(len(hours))
    times = np.array([d[0].timestamp() for d in decisions])
    after = np.array([d[2] for d in decisions])
    # last decision that ended at or before the start of each hour
    idx = np.searchsorted(times, hours * 3600.0, side="right") - 1
    pos = np.where(idx >= 0, after[np.clip(idx, 0, None)], decisions[0][1])
    return pos


def capture_for_leg(pos: np.ndarray, prices: np.ndarray, direction: str, change: float) -> dict:
    """pos[i] held over hour i, priced from prices[i] to prices[i + 1]."""
    held = pos[:-1]
    move = np.diff(prices)
    sign = 1 if direction == "up" else -1
    n = len(held)
    if n == 0:
        return {}
    notional = np.abs(held * prices[:-1])
    aligned = np.sign(held) == sign
    against = np.sign(held) == -sign
    pnl = float(np.nansum(held * move))
    max_notional = float(np.nanmax(notional)) if n else 0.0
    return {
        "time_aligned": float(aligned.mean()),
        "time_against": float(against.mean()),
        "max_notional": max_notional,
        "avg_aligned_notional": float(notional[aligned].mean()) if aligned.any() else 0.0,
        "pnl_mark": pnl,
        "capture_ratio": pnl / (max_notional * abs(change)) if max_notional > 0 and change else None,
    }


def refine_pivot(series, day_start: datetime, kind: str) -> datetime:
    """Hour of the high (kind H) or low (kind L) within the 24h starting at day_start, from hourly candles."""
    h0 = int(day_start.timestamp() // 3600)
    idx = series.index(np.arange(h0, h0 + 24) * 60)
    ok = (idx >= 0) & (idx <= series.last_index)
    if not ok.any():
        return day_start
    idx = idx[ok]
    values = series.h[idx] if kind == "H" else series.l[idx]
    if np.isnan(values).all():
        return day_start
    i = int(np.nanargmax(values) if kind == "H" else np.nanargmin(values))
    return datetime.fromtimestamp(int(series.t0 + idx[i] * series.step) * 60, tz=UTC)


def compute_wallet_captures(session: Session, wallet_id: int, as_of: datetime, coverage_start: datetime | None,
                            market) -> int:
    rows = session.execute(text("""
        SELECT coin, ended_at, pos_before, pos_after FROM trade_decision WHERE wallet_id = :w
        ORDER BY coin, ended_at"""), {"w": wallet_id}).all()
    by_coin: dict[str, list[tuple[datetime, float, float]]] = {}
    for coin, t, before, after in rows:
        by_coin.setdefault(coin, []).append((t, float(before), float(after)))
    session.execute(text("DELETE FROM leg_capture WHERE wallet_id = :w"), {"w": wallet_id})
    out = []
    now = datetime.now(UTC)
    for coin, decisions in by_coin.items():
        macro = market.get((coin, "macro"))
        hourly = market.get((coin, "short"))  # 1h series
        if macro is None or hourly is None:
            continue
        series = hourly[0]
        for leg in macro[1]:
            if leg.end_ts < LEGS_SINCE:
                continue
            leg_start = refine_pivot(series, leg.start_ts, "L" if leg.direction == "up" else "H")
            leg_end = refine_pivot(series, leg.end_ts, "H" if leg.direction == "up" else "L")
            start = max(leg_start, coverage_start) if coverage_start else leg_start
            end = min(leg_end, as_of)
            if end - start < timedelta(days=2):
                continue
            h0, h1 = int(start.timestamp() // 3600), int(end.timestamp() // 3600)
            hours = np.arange(h0, h1 + 1)
            idx = series.index(hours * 60)
            if idx.min() < 0 or idx.max() > series.last_index:
                continue
            prices = series.c[idx]
            if np.isnan(prices).mean() > 0.05:
                continue
            prices = np.where(np.isnan(prices), np.nan, prices)
            # forward-fill isolated gaps
            for i in range(1, len(prices)):
                if np.isnan(prices[i]):
                    prices[i] = prices[i - 1]
            pos = position_path(decisions, hours)
            m = capture_for_leg(pos, prices, leg.direction, leg.change)
            if not m or m["max_notional"] == 0:
                continue
            full = (coverage_start is None or coverage_start <= leg_start) and as_of >= leg_end
            out.append({"w": wallet_id, "coin": coin, "ls": leg_start, "le": leg_end, "dir": leg.direction,
                        "chg": leg.change, "conf": leg.confirmed, "cov": "full" if full else "partial",
                        **m, "v": CAPTURE_VERSION, "now": now})
    if out:
        session.execute(text("""
            INSERT INTO leg_capture (wallet_id, coin, leg_start, leg_end, direction, leg_change, confirmed, coverage,
              time_aligned, time_against, max_notional, avg_aligned_notional, pnl_mark, capture_ratio, version,
              computed_at)
            VALUES (:w, :coin, :ls, :le, :dir, :chg, :conf, :cov, :time_aligned, :time_against, :max_notional,
              :avg_aligned_notional, :pnl_mark, :capture_ratio, :v, :now)"""), out)
    return len(out)
