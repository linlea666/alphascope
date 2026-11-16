from datetime import UTC, datetime, timedelta

import pytest

from app.quant.returns import Point, window_return
from app.research.swings import Bar, poisson_binomial_tail, zigzag

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def bars(prices):
    """One bar per day with high = low = price (enough to test pivot logic)."""
    return [Bar(T0 + timedelta(days=i), p, p) for i, p in enumerate(prices)]


def test_zigzag_finds_alternating_pivots_and_provisional_tail():
    # 100 -> 130 (+30%) -> 100 (-23%) -> 120 (+20%, still rising: provisional)
    legs = zigzag(bars([100, 110, 130, 120, 100, 105, 120]), 0.15)
    assert [(l.direction, l.start_px, l.end_px, l.confirmed) for l in legs] == [
        ("up", 100, 130, True),
        ("down", 130, 100, True),
        ("up", 100, 120, False),
    ]


def test_zigzag_ignores_moves_below_threshold():
    legs = zigzag(bars([100, 108, 101, 109, 102]), 0.15)
    assert legs == []


def test_zigzag_uses_highs_and_lows():
    b = [Bar(T0, 100, 95), Bar(T0 + timedelta(days=1), 140, 120), Bar(T0 + timedelta(days=2), 118, 100)]
    legs = zigzag(b, 0.15)
    assert legs[0].start_px == 95 and legs[0].end_px == 140  # low of day 0 -> high of day 1


def test_zigzag_confirmed_legs_are_stable_when_data_extends():
    base = [100, 130, 100, 125, 95]
    a = zigzag(bars(base), 0.15)
    b = zigzag(bars(base + [80, 90]), 0.15)
    assert [l for l in a if l.confirmed] == [l for l in b if l.confirmed][: len([l for l in a if l.confirmed])]


def test_poisson_binomial_tail():
    assert poisson_binomial_tail([0.5, 0.5], 2) == pytest.approx(0.25)
    assert poisson_binomial_tail([0.5, 0.5], 1) == pytest.approx(0.75)
    assert poisson_binomial_tail([0.2, 0.9, 0.5], 0) == pytest.approx(1.0)
    assert poisson_binomial_tail([0.2, 0.9, 0.5], 3) == pytest.approx(0.2 * 0.9 * 0.5)


def pts(rows, step=timedelta(days=2)):
    return [Point(T0 + i * step, av, pnl) for i, (av, pnl) in enumerate(rows)]


def test_window_return_anchors_and_excludes_flows():
    p = pts([(1000, 0), (1100, 100), (2100, 100), (2310, 310), (2310, 310)])  # +10%, deposit, +10%
    r = window_return(p, T0, T0 + timedelta(days=6), timedelta(days=1))
    assert r["twr"] == pytest.approx(1.1 * 1.1 - 1)
    assert r["net_flows"] == pytest.approx(1000)
    assert r["anchor_start"] == T0 and r["anchor_end"] == T0 + timedelta(days=6)


def test_window_return_requires_points_near_both_ends():
    p = pts([(1000, 0), (1100, 100), (1200, 200)])
    assert window_return(p, T0, T0 + timedelta(days=30), timedelta(days=5)) is None
