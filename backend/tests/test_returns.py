from datetime import UTC, datetime, timedelta

import pytest

from app.ingest.portfolio_ingest import parse_portfolio
from app.quant.returns import METRIC_WINDOWS, Point, compute_series_metrics
from tests.conftest import load_fixture

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def series(rows, step=timedelta(hours=12)):
    return [Point(T0 + i * step, av, pnl) for i, (av, pnl) in enumerate(rows)]


def now_after(points):
    return points[-1].ts


def test_deposits_and_withdrawals_are_not_returns():
    # +10% from trading, then a 1000 deposit with no pnl, then a 500 withdrawal with no pnl
    pts = series([(1000, 0), (1100, 100), (2100, 100), (1600, 100)])
    m = compute_series_metrics(pts, None, now_after(pts))
    assert m.twr == pytest.approx(0.10)
    assert m.net_flows == pytest.approx(500)
    assert m.pnl == pytest.approx(100)
    # naive equity ratio would claim +60%
    assert pts[-1].account_value / pts[0].account_value - 1 == pytest.approx(0.6)


def test_modified_dietz_with_mid_period_flow():
    pts = series([(1000, 0), (2100, 100), (2100, 100), (2100, 100)])  # 1000 deposit + 100 pnl in one period
    m = compute_series_metrics(pts, None, now_after(pts))
    assert m.twr == pytest.approx(100 / (1000 + 0.5 * 1000))


def test_drawdown_on_twr_index():
    pts = series([(1000, 0), (1100, 100), (880, -120), (924, -76)])  # +10%, -20%, +5%
    m = compute_series_metrics(pts, None, now_after(pts))
    assert m.mdd == pytest.approx(-0.20)
    assert m.twr == pytest.approx(1.1 * 0.8 * 1.05 - 1)
    assert m.pnl_mdd_usd == pytest.approx(-220)
    assert m.positive_period_ratio == pytest.approx(2 / 3)


def test_low_capital_periods_are_skipped_not_divided():
    # $1 -> $5 (+2 pnl): capital base of $2 is below the floor; then a deposit with no pnl, then 3x +10%
    pts = series([(1, 0), (5, 2), (1000, 2), (1100, 102), (1210, 212), (1331, 333)])
    m = compute_series_metrics(pts, None, now_after(pts))
    assert "skipped_low_capital_periods:1" in m.notes
    assert m.twr == pytest.approx(1.1 ** 3 - 1)


def test_window_coverage_is_reported_honestly():
    pts = series([(1000, 0), (1010, 10), (1020, 20), (1030, 30)], step=timedelta(days=10))
    m = compute_series_metrics(pts, timedelta(days=90), now_after(pts))
    assert m.covered is False and "history_shorter_than_window" in m.notes
    assert m.reliability != "high"
    m30 = compute_series_metrics(pts, timedelta(days=15), now_after(pts))
    assert m30.covered is True
    assert m30.span_start == pts[1].ts  # anchored at the last point before the window start


def test_too_few_periods_gives_no_twr():
    pts = series([(1000, 0), (1100, 100)])
    m = compute_series_metrics(pts, None, now_after(pts))
    assert m.twr is None and "too_few_periods" in m.notes and m.reliability == "low"


def test_wipeout_and_large_flows_are_low_reliability():
    pts = series([(1000, 0), (0, -1000), (5000, -1000), (5100, -900)])
    m = compute_series_metrics(pts, None, now_after(pts))
    assert "wipeout_period" in m.notes and m.reliability == "low"


def test_real_portfolio_fixture():
    parsed = parse_portfolio(load_fixture("portfolio_sample.json"))
    assert {"day", "week", "month", "allTime"} <= parsed.keys()
    pts = parsed["allTime"]
    assert len(pts) > 50 and all(a.ts < b.ts for a, b in zip(pts, pts[1:]))
    now = pts[-1].ts
    for window, (name, lookback) in METRIC_WINDOWS.items():
        m = compute_series_metrics(parsed[name], lookback, now)
        assert m.n_points >= 2
        assert m.reliability in {"high", "medium", "low"}
