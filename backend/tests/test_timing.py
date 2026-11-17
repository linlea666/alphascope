from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.research.swings import Leg
from app.research.timing import (Pivot, Scored, Series, benjamini_hochberg, location, match_pivot, permutation_test,
                                 pivots_from_legs)

T0 = datetime(2026, 7, 1, tzinfo=UTC)


def test_location_buy_low_sell_high():
    buy = np.array([True, True, False, False])
    price = np.array([100.0, 110.0, 110.0, 100.0])
    wmax = np.full(4, 110.0)
    wmin = np.full(4, 100.0)
    assert location(buy, price, wmax, wmin).tolist() == [1.0, 0.0, 1.0, 0.0]
    # a fill slightly outside the candle range is clipped; a flat window has no defined location
    assert location(np.array([True]), np.array([99.0]), np.array([110.0]), np.array([100.0]))[0] == 1.0
    assert np.isnan(location(np.array([True]), np.array([100.0]), np.array([100.0]), np.array([100.0]))[0])


def test_benjamini_hochberg_matches_reference():
    q = benjamini_hochberg([0.01, 0.04, 0.03, 0.5])
    assert q == pytest.approx([0.04, 0.0533333, 0.0533333, 0.5], rel=1e-4)
    assert benjamini_hochberg([]) == []


def test_pivots_and_matching_tolerances():
    legs = [Leg("up", T0, 100.0, T0 + timedelta(days=10), 150.0, True),
            Leg("down", T0 + timedelta(days=10), 150.0, T0 + timedelta(days=20), 120.0, True)]
    top, bottom = pivots_from_legs(legs)
    assert (top.kind, top.px, top.amplitude) == ("H", 150.0, 50.0)
    assert bottom.kind == "L" and bottom.amplitude == 30.0
    m = int((T0 + timedelta(days=10)).timestamp() // 60)
    # sell within 20% of the 10-day leg (2 days) and 25% of the amplitude (>= 137.5) hits the top
    assert match_pivot([top, bottom], m + 24 * 60, 140.0, buy=False, step=60) == top
    assert match_pivot([top, bottom], m + 3 * 24 * 60, 140.0, buy=False, step=60) is None  # too late
    assert match_pivot([top, bottom], m, 130.0, buy=False, step=60) is None  # too far below the top
    assert match_pivot([top, bottom], m, 150.0, buy=True, step=60) is None  # buying the top is not a hit


def make_series(prices: np.ndarray, half: int) -> Series:
    n = len(prices)
    w = 2 * half + 1
    wmax = np.full(n, np.nan)
    wmin = np.full(n, np.nan)
    wmax[half:n - half] = np.lib.stride_tricks.sliding_window_view(prices, w).max(axis=1)
    wmin[half:n - half] = np.lib.stride_tricks.sliding_window_view(prices, w).min(axis=1)
    return Series(int(T0.timestamp() // 60), 60, prices, prices, prices, wmax, wmin)


def test_permutation_detects_bottom_buyer_and_not_random_buyer():
    # Random walk (not periodic: a periodic series would let shifts by whole periods hit bottoms again).
    walk = np.random.default_rng(42).normal(0, 1, 2000).cumsum()
    prices = 1000 + walk
    s = make_series(prices, half=12)
    market = {("BTC", "short"): (s, [], [])}
    t0 = s.t0
    lows = [h for h in range(30, 1950) if prices[h] == s.wmin[h]][:40]  # true local lows of the ±12h window

    def scored(hours):
        return [Scored(i, "BTC", "short", t0 + int(h) * 60, True, 1.0,
                       float(location(np.array([True]), prices[h:h + 1], s.wmax[h:h + 1], s.wmin[h:h + 1])[0]),
                       "scored", None) for i, h in enumerate(hours)]

    span = (t0 + 20 * 60, t0 + 1980 * 60)
    null_mean, p, null_ext, null_q = permutation_test(scored(lows), market, span, np.random.default_rng(1))
    assert len(lows) >= 20 and p < 0.01 and null_mean == pytest.approx(0.5, abs=0.15)
    assert 0 < null_ext < 0.5  # random times rarely land in the best 20% of the window
    assert len(null_q) == 21 and null_q == sorted(null_q) and null_q[0] <= null_mean <= null_q[-1]

    rand_hours = np.random.default_rng(3).integers(30, 1950, size=40)
    _, p_noise, _, _ = permutation_test(scored(rand_hours), market, span, np.random.default_rng(2))
    assert p_noise > 0.05


def test_pivot_dataclass_is_hashable_for_sets():
    p = Pivot("H", 1, 1.0, 1.0, 1.0, True)
    assert {p, p} == {p}
