import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.factors.compute import Cyc, Extras, compute, wilson_lo
from app.factors.run import run_styles
from app.factors.styles import BY_KEY, evaluate, forward_check
from app.main import app

NOW = datetime(2026, 11, 17, tzinfo=UTC)


def cyc(days_ago: float, net: float, coin="BTC", direction="long", scale="swing", notional=1000.0, hours=48.0) -> Cyc:
    closed = NOW - timedelta(days=days_ago)
    return Cyc(coin, direction, closed - timedelta(hours=hours), closed, scale, notional, net, hours)


def test_factor_values():
    cs = [cyc(10, 100), cyc(40, -50), cyc(70, 30, coin="ETH", direction="short", scale="scalp"), cyc(100, 20)]
    f, meta = compute(cs, Extras(base_equity=1000, liquidations=1, open_upl=-200.0, open_positions=2), NOW)
    v = {k: x[0] for k, x in f.items()}
    assert v["n_closed"] == 4 and v["net_pnl"] == 100 and v["win_rate"] == 0.75
    assert v["win_rate_lo"] == pytest.approx(wilson_lo(3, 4))
    assert v["profit_factor"] == pytest.approx(150 / 50) and v["top1_share"] == pytest.approx(100 / 150)
    assert v["months"] == 4 and v["positive_month_ratio"] == 0.75
    # cumulative in close order: 20, 50, 0, 100 -> peak 50 then 0: drawdown 50 on a 1000 + 100 base
    assert v["max_dd_usd"] == 50 and v["dd_ratio"] == pytest.approx(50 / 1100)
    assert v["short_share"] == 0.25 and v["short_net"] == 30 and v["scalp_share"] == 0.25
    assert meta["top_coin"] == "BTC" and v["top_coin_n"] == 3 and v["liq_rate"] == 0.25
    assert v["open_loss_ratio"] == pytest.approx(0.2) and f["open_loss_ratio"][1] == 2
    empty, _ = compute([], Extras(), NOW)
    assert empty == {"n_closed": (0.0, 0)}


def factors(**kv):
    return {k: (v, 50) for k, v in kv.items()}


def test_style_status_rules():
    steady = BY_KEY["steady"]
    good = factors(n_closed=60, months=8, positive_month_ratio=0.8, profit_factor=2.0, top1_share=0.1, dd_ratio=0.1,
                   liq_rate=0.0, open_loss_ratio=0.05)
    assert evaluate(steady, good)["status"] == "candidate"
    # an unknown open book (never polled) keeps it at watch; a big open loss too
    assert evaluate(steady, {**good, "open_loss_ratio": (None, 0)})["status"] == "watch"
    assert evaluate(steady, {**good, "open_loss_ratio": (0.9, 2), "dd_ratio": (0.5, 50)})["status"] == "none"
    assert evaluate(steady, {**good, "n_closed": (10, 10)})["status"] == "insufficient"
    risky = evaluate(BY_KEY["high_risk"], factors(n_closed=20, liq_rate=0.0, median_leverage=50, top1_share=0.1))
    assert risky["status"] == "candidate" and risky["evidence"][-1]["any"][1]["ok"] is True
    # two-rule styles have no "one rule short" watch
    assert evaluate(BY_KEY["trend"], factors(big_n=10, big_net=5, q_macro=0.5, q_swing=0.5, capture_avg=0.1)
                    )["status"] == "none"
    assert evaluate(BY_KEY["trend"], factors(big_n=10, big_net=5, q_macro=0.05))["status"] == "candidate"


def test_forward_check():
    cut = NOW - timedelta(days=90)
    later = [cyc(d, 10, scale="swing") for d in (5, 10, 20, 30, 40)] + [cyc(50, -20, scale="scalp")]
    ok = forward_check(BY_KEY["swing"], later + [cyc(200, -999)], cut, None)
    assert ok["passed"] and ok["forward_n"] == 5 and ok["forward_net"] == 50  # the scalp loss is another kind
    assert forward_check(BY_KEY["steady"], later, cut, None)["passed"]  # all kinds: net 30, pf 50 / 20
    assert not forward_check(BY_KEY["steady"], later[:3], cut, None)["passed"]  # 3 later cycles: too few
    losing = [cyc(d, -10) for d in (5, 10, 20, 30, 40)] + [cyc(45, 30)]
    assert not forward_check(BY_KEY["steady"], losing, cut, None)["passed"]  # the edge did not persist


def _seed(sf, open_upl: float):
    """An OKX trader: 14 months of steady swing trading, polled by the radar with the given open PnL."""
    with sf() as s:
        wid = s.execute(text("""
            INSERT INTO wallet (address, venue, venue_uid, handle, first_seen_at, first_seen_source, last_seen_at,
                                present_in_latest)
            VALUES ('okx:1000001', 'okx', '1000001', 'H', now(), 't', now(), false) RETURNING id""")).scalar_one()
        s.execute(text("""INSERT INTO okx_trader (wallet_id, history_status, pool_status, priority, target_days,
                          n_positions, failure_count, asset, updated_at)
                          VALUES (:w, 'source_complete', 'active', 1, 365, 0, 0, 10000, now())"""), {"w": wid})
        now = datetime.now(UTC)
        rows = []
        for i in range(84):  # every 5 days for 14 months; 2 of 3 win
            closed = now - timedelta(days=5 * i + 1)
            net = 60 if i % 3 else -30
            rows.append({"w": wid, "o": closed - timedelta(days=3), "c": closed, "net": net})
        s.execute(text("""
            INSERT INTO okx_position (wallet_id, pos_id, inst_id, coin, ct_type, direction, open_time, close_time,
              open_avg_px, close_avg_px, max_size, closed_size, max_notional, pnl, realized_pnl, fee, funding,
              close_type, liquidated, raw, fetched_at)
            SELECT :w, 'old' || g, 'BTC-USDT-SWAP', 'BTC', 'linear', 'long', now() - interval '400 days',
                   now() - interval '399 days', 100, 101, 1, 1, 100, 1, 1, 0, 0, 'allClose', false, '{}', now()
            FROM generate_series(1, 3) g"""), {"w": wid})
        s.execute(text("""
            INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
              scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions, realized_pnl, fees)
            VALUES (:w, 'BTC', 'okx1', 'long', :o, :c, 'closed', true, 'swing', 72, 100, 101, 1, 5000, 0, 2, :net, 0)"""),
            rows)
        s.execute(text("""INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due,
                          last_observed_at, stale_after, failures, n_positions)
                          VALUES (:w, 'okx', 'hot', 'hot', now(), now(), now(), now() + interval '1 hour', 0, 1)"""),
                  {"w": wid})
        s.execute(text("""INSERT INTO trader_position (wallet_id, coin, venue, szi, notional_usd, upl, opened_at_source,
                          first_seen_at, observed_at) VALUES (:w, 'ETH', 'okx', 1, 1000, :u, 'source', now(), now())"""),
                  {"w": wid, "u": open_upl})
        s.commit()
    return wid


def test_run_styles_verifies_out_of_sample_and_blocks_open_losses(sf):
    _seed(sf, open_upl=-100)
    r = run_styles(sf)
    assert r["traders"] == 1 and r["styles"]["steady"] == {"verified": 1} and r["styles"]["swing"] == {"verified": 1}
    with sf() as s:
        oos = s.execute(text("SELECT oos FROM style_tag WHERE style = 'steady'")).scalar_one()
        assert oos["passed"] and oos["forward_n"] >= 5
        assert float(s.execute(text("""SELECT value FROM factor_value WHERE factor = 'open_loss_ratio'
                                       AND time_window = 'all'""")).scalar_one()) == pytest.approx(0.01)
        s.execute(text("UPDATE trader_position SET upl = -9000"))
        s.commit()
    r = run_styles(sf)
    # 90% of assets in open losses vetoes every ability label, whatever the realized record; the risk flag stays
    assert set(r["styles"]) <= {"high_risk"}


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_discover_api(client, sf):
    wid = _seed(sf, open_upl=-100)
    run_styles(sf)
    styles = {s["key"]: s for s in client.get("/api/v1/discover/styles").json()["styles"]}
    assert styles["steady"]["counts"] == {"verified": 1} and styles["high_risk"]["risk_flag"]
    board = client.get("/api/v1/discover", params={"style": "steady", "status": "verified"}).json()["rows"]
    assert board[0]["address"] == "okx:1000001" and "swing" in board[0]["other"]
    assert board[0]["factors"]["n_closed"]["value"] == 84
    assert client.get("/api/v1/discover", params={"style": "nope"}).status_code == 422
    prof = client.get("/api/v1/discover/profile/okx:1000001").json()
    assert {"all", "90d"} <= set(prof["factors"]["net_pnl"]) and prof["styles"][0]["status"] == "verified"
    # week board: 1-2 closed positions in 7 days -> below the 5-position gate; add a busy week with a liquidation
    with sf() as s:
        now = datetime.now(UTC)
        raw = json.dumps({})
        for i in range(6):
            s.execute(text("""
                INSERT INTO okx_position (wallet_id, pos_id, inst_id, coin, ct_type, direction, open_time, close_time,
                  open_avg_px, close_avg_px, max_size, closed_size, max_notional, pnl, realized_pnl, fee, funding,
                  close_type, liquidated, raw, fetched_at)
                VALUES (:w, :p, 'BTC-USDT-SWAP', 'BTC', 'linear', 'long', :o, :c, 100, 110, 1, 1, 100, 10, 10, 0, 0,
                        'allClose', :liq, CAST(:raw AS jsonb), now())"""),
                {"w": wid, "p": str(i), "o": now - timedelta(days=2), "c": now - timedelta(days=1), "liq": False,
                 "raw": raw})
        s.commit()
    top = client.get("/api/v1/discover/top", params={"period": "7d"}).json()
    assert [r["address"] for r in top["okx"]] == ["okx:1000001"] and top["okx"][0]["net"] == 60
    with sf() as s:
        s.execute(text("UPDATE okx_position SET liquidated = true WHERE pos_id = '0'"))
        s.commit()
    assert client.get("/api/v1/discover/top", params={"period": "7d"}).json()["okx"] == []
