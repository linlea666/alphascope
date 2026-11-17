from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.factors import copyability, health
from app.factors.copy_run import run_copy_analysis
from app.research.behavior import fingerprint
from app.sim.backtest import BacktestParams, backtest
from tests.test_zones_api import client  # noqa: F401  (fixture re-export)

NOW = datetime(2026, 11, 17, tzinfo=UTC)
T0 = NOW - timedelta(days=10)
ADDR = "0x" + "c" * 40


def seed(sf, decisions, equity=100_000, bars=(), hours=()):
    """decisions: (time, vwap, pos_before, pos_after); bars: 1m (time, open); hours: 1h (time, high, low, close)."""
    with sf() as s:
        wid = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
            present_in_latest, account_value) VALUES (:a, now(), 't', now(), true, :e) RETURNING id"""),
            {"a": ADDR, "e": equity}).scalar_one()
        for ts in (NOW - timedelta(days=40), NOW):
            s.execute(text("""INSERT INTO equity_point (wallet_id, series, ts, account_value, pnl, fetched_at)
                VALUES (:w, 'allTime', :t, :e, 0, now())"""), {"w": wid, "t": ts, "e": equity})
        for t, px, before, after in decisions:
            kind = "open" if before == 0 else "close" if after == 0 else "add"
            s.execute(text("""INSERT INTO trade_decision (wallet_id, coin, version, kind, side, started_at, ended_at,
                n_fills, size, notional, vwap, pos_before, pos_after, realized_pnl, fee)
                VALUES (:w, 'BTC', 'p1', :k, :sd, :t, :t, 1, :sz, :n, :px, :b, :a, 0, 0)"""),
                {"w": wid, "k": kind, "sd": "buy" if after > before else "sell", "t": t, "sz": abs(after - before),
                 "n": abs(after - before) * px, "px": px, "b": before, "a": after})
        for t, o in bars:
            s.execute(text("""INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
                VALUES ('binance_um', 'BTC', '1m', :t, :o, :o, :o, :o, 1, 1, now())"""), {"t": t, "o": o})
        for t, hi, lo, c in hours:
            s.execute(text("""INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
                VALUES ('binance_um', 'BTC', '1h', :t, :c, :h, :l, :c, 1, 1, now())"""), {"t": t, "h": hi, "l": lo, "c": c})
        s.commit()
    return wid


def test_backtest_sizes_proportionally_and_uses_delayed_prices(sf):
    close = T0 + timedelta(days=2)
    wid = seed(sf, [(T0, 80000, 0, 1), (close, 88000, 1, 0)],
               bars=[(T0 + timedelta(minutes=1), 80100), (close + timedelta(minutes=1), 87900)])
    with sf() as s:
        now0 = backtest(s, wid, BacktestParams(days=30, delay_seconds=0, capital=10_000), now=NOW)
        late = backtest(s, wid, BacktestParams(days=30, delay_seconds=30, capital=10_000), now=NOW)
    # 1 BTC = $80k on a $100k account = 80% -> $8k on $10k = 0.1 BTC at the leader's own prices
    expect = 0.1 * (88000 * 0.9997 - 80000 * 1.0003) - 0.1 * (80000 * 1.0003 + 88000 * 0.9997) * 4.5e-4
    assert now0["ok"] and now0["pnl"] == pytest.approx(expect, rel=1e-3) and now0["trades"] == 2
    assert late["pnl"] < now0["pnl"] and late["resolution"] == {"BTC": "1m"}  # 30 s later: the next 1m open


def test_backtest_hourly_liquidation(sf):
    hour = (T0 + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    wid = seed(sf, [(T0, 80000, 0, 10)], equity=10_000,  # 80x leader -> capped at 10x of our capital
               hours=[(hour, 80000, 71000, 75000)])
    with sf() as s:
        r = backtest(s, wid, BacktestParams(days=30, delay_seconds=0), now=NOW)
    assert r["stopped"] == "liquidated" and r["liquidations"] == 1 and r["capped_share"] == 1.0


def test_copyability_score_and_insufficient():
    assert copyability.score({"n_closed": 5}, None, None, False, None, None)["status"] == "insufficient"
    f = {"n_closed": 120, "dd_ratio": 0.1, "liq_rate": 0, "median_leverage": 5, "days_since_last": 1, "top1_share": 0.15}
    bt = {"ret": 0.5, "mdd": 0.0, "delay_curve": [[0, 0.2], [300, 0.19]]}
    r = copyability.score(f, 0.005, 0.1, True, bt, bt)
    assert r["status"] == "scored" and r["grade"] == "A" and r["score"] >= 90
    assert {c["key"] for c in r["components"]} == {"skill", "copy_return", "drawdown", "risk", "latency", "activity",
                                                   "concentration"}
    weak = copyability.score({**f, "liq_rate": 0.1, "median_leverage": 40}, None, -0.1, False,
                             {"ret": -0.2, "mdd": -0.6, "delay_curve": []}, None)
    assert weak["grade"] == "D"


def cyc(days_ago, net, hours=10, scale="swing", direction="long", notional=1000):
    return {"closed_at": NOW - timedelta(days=days_ago), "net": net, "hours": hours, "scale": scale,
            "direction": direction, "notional": notional}


def test_health_statuses():
    good_prior = [cyc(40 + i, 100 if i % 4 else -50) for i in range(40)]
    assert health.assess(good_prior + [cyc(5 + i, 100 if i % 4 else -50) for i in range(10)], NOW - timedelta(days=1),
                         NOW)["status"] == "stable"
    bad = health.assess(good_prior + [cyc(5 + i, -200 if i % 3 else 50) for i in range(10)], NOW, NOW)
    assert bad["status"] == "declining" and {s["key"] for s in bad["signals"]} >= {"win_rate_down", "net_negative"}
    drift = health.assess(good_prior + [cyc(5 + i, 100, hours=0.5, scale="scalp") for i in range(10)], NOW, NOW)
    assert drift["status"] == "drifting"
    assert health.assess(good_prior, NOW - timedelta(days=30), NOW)["status"] == "dormant"
    assert health.assess(good_prior[:5], NOW, NOW)["status"] == "insufficient"


def test_behavior_flags(sf):
    wid = seed(sf, [])
    with sf() as s:
        for i in range(24):  # winners closed after 2h, losers held for 2 days
            win = i % 2 == 0
            o = T0 - timedelta(days=60 - i * 2)
            s.execute(text("""INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at,
                status, complete, scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions,
                realized_pnl, fees) VALUES (:w, 'BTC', 'p1', 'long', :o, :c, 'closed', true, 'short', :h, 1, 1, 1,
                1000, 1, 2, :pnl, 0)"""), {"w": wid, "o": o, "c": o + timedelta(hours=2 if win else 48),
                                          "h": 2 if win else 48, "pnl": 100 if win else -60})
        s.commit()
        b = fingerprint(s, wid, "hyperliquid", NOW)
    assert b["closed"] == 24 and b["hold_win_h"] == 2 and b["hold_loss_h"] == 48
    assert any(f["key"] == "holds_losers" and f["good"] is False for f in b["flags"])
    assert any(f["key"] == "cuts_losses" for f in b["flags"])  # every loss the same size


def test_batch_and_board(sf, client):  # noqa: F811
    close = NOW - timedelta(days=3)
    wid = seed(sf, [(T0, 80000, 0, 1), (close, 88000, 1, 0)],
               bars=[(T0 + timedelta(minutes=1), 80100), (close + timedelta(minutes=1), 87900)])
    with sf() as s:
        s.execute(text("INSERT INTO watch_group (owner, name) VALUES ('default', 'g')"))
        s.execute(text("INSERT INTO watch_item (group_id, wallet_id) SELECT id, :w FROM watch_group"), {"w": wid})
        s.commit()
    r = run_copy_analysis(sf, now=NOW, sender=lambda *a, **k: None)
    assert r["pool"] == 1 and r["backtested_30d"] == 1
    board = client.get("/api/v1/copy-board", params={"window": 30, "min_trades": 0}).json()
    assert board["rows"][0]["address"] == ADDR and board["rows"][0]["ret"] > 0
    assert board["rows"][0]["delay_curve"][0][0] == 0
    c = client.get(f"/api/v1/wallets/{ADDR}/copyability").json()
    assert c["score"]["status"] == "insufficient" and [b["window_days"] for b in c["backtests"]] == [7, 30, 90]
    assert client.get(f"/api/v1/wallets/{ADDR}/behavior").status_code == 200
