from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import app
from app.sim.engine import MAX_LEVERAGE, Params, coin_leverage, fill, plan_orders, step_simulations, target_sizes

T0 = datetime(2026, 11, 17, 12, 0, 30, tzinfo=UTC)
MIDS = {"BTC": D(80000), "ETH": D(3000)}


def test_target_sizing_modes_and_leverage_cap():
    leader = {"BTC": D(2)}  # $160k on $400k equity = 40% of equity
    p = Params()
    t = target_sizes(p, leader, D(400000), D(10000), MIDS, {"BTC"}, {})
    assert t["BTC"] * MIDS["BTC"] == pytest.approx(D(4000))
    t = target_sizes(Params(sizing="fixed_multiple", size_factor=D("0.01")), leader, D(400000), D(10000), MIDS, {"BTC"}, {})
    assert t["BTC"] == pytest.approx(D("0.02"))
    t = target_sizes(Params(sizing="fixed_cap", cap_per_coin=D(1000)), leader, D(400000), D(10000), MIDS, {"BTC"}, {})
    assert t["BTC"] * MIDS["BTC"] == pytest.approx(D(1000))
    # 20x leader exposure with max leverage 10 -> scaled down to 10x equity
    t = target_sizes(Params(), {"BTC": D(100)}, D(400000), D(10000), MIDS, {"BTC"}, {})
    assert t["BTC"] * MIDS["BTC"] == pytest.approx(D(100000))
    # not followed (pre-existing) -> 0; held coin without leader position -> close
    t = target_sizes(p, leader, D(400000), D(10000), MIDS, set(), {"ETH": D(1)})
    assert t == {"BTC": D(0), "ETH": D(0)} or t == {"ETH": D(0)}


def test_leader_leverage_follow_and_caps():
    follow = Params(leverage_mode="leader", max_leverage=D(MAX_LEVERAGE))
    # following: the leader's 100x stands where the coin allows it, else the coin's maximum
    assert coin_leverage(follow, "BTC", {"BTC": 100}, {"BTC": 125}) == 100
    assert coin_leverage(follow, "BTC", {"BTC": 100}, {"BTC": 40}) == 40
    # an explicit cap still applies
    assert coin_leverage(Params(leverage_mode="leader", max_leverage=D(50)), "BTC", {"BTC": 100}, {"BTC": 125}) == 50
    # unknown leader leverage: our cap, at most the unknown-coin default (20x), never the coin maximum
    assert coin_leverage(follow, "BTC", {}, {"BTC": 125}) == 20
    assert coin_leverage(Params(leverage_mode="leader", max_leverage=D(10)), "BTC", {"BTC": None}, {"BTC": 125}) == 10
    # 100x leader, follow mode: margin for $160k notional is $1.6k, not scaled down on a $10k account
    t = target_sizes(follow, {"BTC": D(2)}, D(4000), D(10000), MIDS, {"BTC"}, {}, {"BTC": 100}, {"BTC": 125})
    assert t["BTC"] * MIDS["BTC"] == pytest.approx(D(400000))  # 40x of equity, same as the leader's share
    assert t["BTC"] * MIDS["BTC"] / 100 <= D(10000)


def test_leverage_validation_allows_125x():
    from pydantic import ValidationError

    from app.api.sims import SimParams
    assert SimParams(max_leverage=100, leverage_mode="leader").max_leverage == 100
    assert SimParams(max_leverage=125).max_leverage == 125
    with pytest.raises(ValidationError):
        SimParams(max_leverage=126)


def test_plan_orders_thresholds():
    p = Params(min_trade_usd=D(10), rebalance_pct=D(5))
    assert plan_orders(p, {"BTC": D("0.1001")}, {"BTC": D("0.1")}, MIDS) == {}  # $8 tweak skipped
    assert plan_orders(p, {"BTC": D("0.12")}, {"BTC": D("0.1")}, MIDS) == {"BTC": D("0.02")}
    assert plan_orders(p, {"BTC": D(0)}, {"BTC": D("0.0001")}, MIDS) == {"BTC": D("-0.0001")}  # close always
    assert plan_orders(p, {"BTC": D("-0.1")}, {"BTC": D("0.1")}, MIDS) == {"BTC": D("-0.2")}  # flip always


def test_fill_accounting():
    p = Params(fee_bps=D(10), slippage_bps=D(10))  # 0.1% each
    f = fill(D(0), D(0), D(1), D(100), p)
    assert f.px == D("100.1") and f.fee == pytest.approx(D("0.1001")) and f.entry_after == D("100.1")
    f2 = fill(D(1), D(100), D(1), D(110), p)  # add at 110.11 -> average
    assert f2.entry_after == pytest.approx((D(100) + D("110.11")) / 2)
    f3 = fill(D(2), D(100), D(-1), D(120), p)  # reduce at 119.88
    assert f3.realized == pytest.approx(D("19.88")) and f3.entry_after == D(100) and f3.size_after == D(1)
    f4 = fill(D(1), D(100), D(-3), D(90), p)  # flip: close 1 at 89.91, open short 2
    assert f4.realized == pytest.approx(D("-10.09")) and f4.size_after == D(-2) and f4.entry_after == D("89.91")


def seed(sf, positions, equity="400000", capital=10000, **kw):
    with sf() as s:
        wid = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
            present_in_latest) VALUES (:a, now(), 't', now(), true) RETURNING id"""), {"a": "0x" + "e" * 40}).scalar_one()
        s.execute(text("INSERT INTO live_account (wallet_id, account_value, fetched_at) VALUES (:w, :e, now())"),
                  {"w": wid, "e": equity})
        set_leader(s, wid, positions)
        cols = {"delay_seconds": 0, "stop_drawdown_pct": None, "copy_existing": False, "max_leverage": 10, **kw}
        sid = s.execute(text("""INSERT INTO copy_sim (owner, name, leader_wallet_id, status, capital, sizing, size_factor,
            max_leverage, follow_adjust, copy_existing, delay_seconds, fee_bps, slippage_bps, min_trade_usd, rebalance_pct,
            stop_drawdown_pct, cash, peak_equity, state)
            VALUES ('default', 't', :w, 'running', :c, 'equity_ratio', 1, :ml, true, :ce, :d, 4.5, 3, 10, 5, :dd, :c, :c, '{}')
            RETURNING id"""), {"w": wid, "c": capital, "ml": cols["max_leverage"], "ce": cols["copy_existing"],
                               "d": cols["delay_seconds"], "dd": cols["stop_drawdown_pct"]}).scalar_one()
        s.commit()
    return wid, sid


def set_leader(s, wid, positions):
    s.execute(text("DELETE FROM live_position WHERE wallet_id = :w"), {"w": wid})
    for coin, szi in positions.items():
        s.execute(text("""INSERT INTO live_position (wallet_id, coin, szi, liq_risk, updated_at) VALUES (:w, :c, :z, false, now())"""),
                  {"w": wid, "c": coin, "z": szi})


def pos(sf, sid):
    with sf() as s:
        return {c: z for c, z in s.execute(text("SELECT coin, size FROM sim_position WHERE sim_id = :s"), {"s": sid}).all()}


def test_flow_baseline_follow_close_funding(sf):
    wid, sid = seed(sf, {"ETH": "10"})  # ETH open before the sim starts
    step_simulations(sf, MIDS, now=T0)
    assert pos(sf, sid) == {}  # pre-existing ETH is not chased
    with sf() as s:
        set_leader(s, wid, {"ETH": "10", "BTC": "2"})  # leader opens BTC after the start
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    assert set(pos(sf, sid)) == {"BTC"} and pos(sf, sid)["BTC"] == pytest.approx(D("0.05"))  # 40% of $10k
    # next hour: funding at +0.01% on a $4k long costs $0.4
    step_simulations(sf, MIDS, funding={"BTC": D("0.0001")}, now=T0 + timedelta(hours=1))
    with sf() as s:
        paid = s.execute(text("SELECT sum(funding) FROM sim_trade WHERE sim_id = :s AND kind = 'funding'"),
                         {"s": sid}).scalar_one()
        assert paid == pytest.approx(D("0.4"), rel=D("0.001"))
        set_leader(s, wid, {"ETH": "0"})  # leader closes everything
        s.commit()
    step_simulations(sf, {**MIDS, "BTC": D(84000)}, now=T0 + timedelta(hours=1, seconds=30))
    assert pos(sf, sid) == {}
    with sf() as s:
        realized = s.execute(text("SELECT sum(realized_pnl) FROM sim_trade WHERE sim_id = :s"), {"s": sid}).scalar_one()
        assert realized == pytest.approx(D("0.05") * (D(84000) * D("0.9997") - D(80000) * D("1.0003")), rel=D("0.000001"))
        assert s.execute(text("SELECT count(*) FROM sim_equity WHERE sim_id = :s"), {"s": sid}).scalar_one() >= 3


def test_delay_manual_stop_and_drawdown(sf):
    wid, sid = seed(sf, {}, delay_seconds=60)
    step_simulations(sf, MIDS, now=T0)
    with sf() as s:
        set_leader(s, wid, {"BTC": "2"})
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    assert pos(sf, sid) == {}  # waiting for the delay
    step_simulations(sf, {"BTC": D(81000)}, now=T0 + timedelta(seconds=95))
    assert "BTC" in pos(sf, sid)
    with sf() as s:
        px = s.execute(text("SELECT px FROM sim_trade WHERE sim_id = :s AND kind = 'trade'"), {"s": sid}).scalar_one()
        assert px == pytest.approx(D(81000) * D("1.0003"))  # filled at the later price
        s.execute(text("""UPDATE copy_sim SET state = jsonb_set(state, '{stop_requested}', 'true') WHERE id = :s"""),
                  {"s": sid})
        s.commit()
    step_simulations(sf, {"BTC": D(81000)}, now=T0 + timedelta(seconds=125))
    assert pos(sf, sid) == {}
    with sf() as s:
        assert tuple(s.execute(text("SELECT status, stop_reason FROM copy_sim WHERE id = :s"), {"s": sid}).one()) == \
            ("stopped", "manual")


def test_drawdown_stop_and_liquidation(sf):
    wid, sid = seed(sf, {}, stop_drawdown_pct=10, max_leverage=20)
    step_simulations(sf, MIDS, now=T0)
    with sf() as s:
        set_leader(s, wid, {"BTC": "100"})  # 20x of leader equity -> capped at 20x ours
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    step_simulations(sf, {"BTC": D(79500)}, now=T0 + timedelta(seconds=60))  # -0.6% * 20x = -12%
    with sf() as s:
        assert tuple(s.execute(text("SELECT status, stop_reason FROM copy_sim WHERE id = :s"), {"s": sid}).one()) == \
            ("stopped", "drawdown")
    wid2 = wid
    with sf() as s:
        s.execute(text("""INSERT INTO copy_sim (owner, name, leader_wallet_id, status, capital, sizing, size_factor,
            max_leverage, follow_adjust, copy_existing, delay_seconds, fee_bps, slippage_bps, min_trade_usd, rebalance_pct,
            cash, peak_equity, state) VALUES ('default', 'liq', :w, 'running', 10000, 'equity_ratio', 1, 40, true, true,
            0, 0, 0, 10, 5, 10000, 10000, '{}')"""), {"w": wid2})
        s.commit()
    step_simulations(sf, MIDS, max_lev={"BTC": 40}, now=T0 + timedelta(minutes=5))  # copies the 20x long
    # -4.5% * 20x = -90%: equity ~$1k is below maintenance (1/80 of ~$190k notional)
    step_simulations(sf, {"BTC": D(76400)}, max_lev={"BTC": 40}, now=T0 + timedelta(minutes=6))
    with sf() as s:
        st = s.execute(text("SELECT status, stop_reason FROM copy_sim WHERE name = 'liq'")).one()
        assert tuple(st) == ("stopped", "liquidated")


@pytest.fixture
def client(sf):
    with sf() as s:
        s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at, present_in_latest)
                          VALUES (:a, now(), 't', now(), true)"""), {"a": "0x" + "f" * 40})
        s.commit()

    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sims_api(client):
    leader = "0x" + "f" * 40
    assert client.post("/api/v1/sims", json={"name": "x", "leader": "0x12"}).status_code == 422
    assert client.post("/api/v1/sims", json={"name": "x", "leader": leader, "sizing": "fixed_cap"}).status_code == 422
    sid = client.post("/api/v1/sims", json={"name": "跟抄底哥", "leader": leader, "capital": 5000}).json()["id"]
    lst = client.get("/api/v1/sims").json()
    assert lst["sims"][0]["equity"] == 5000 and lst["portfolio"]["active"] == 1
    assert client.patch(f"/api/v1/sims/{sid}", json={"action": "pause"}).status_code == 200
    assert client.get(f"/api/v1/sims/{sid}").json()["summary"]["status"] == "paused"
    assert client.patch(f"/api/v1/sims/{sid}", json={"params": {"size_factor": 0.5}}).status_code == 200
    assert client.get(f"/api/v1/sims/{sid}").json()["params"]["size_factor"] == 0.5
    assert client.delete(f"/api/v1/sims/{sid}").status_code == 200
    assert client.get(f"/api/v1/sims/{sid}").status_code == 404


def test_cycles_split_flips_and_attach_funding():
    from app.sim.analytics import cycle_stats, cycles

    t = [T0 + timedelta(hours=i) for i in range(6)]
    rows = [
        {"t": t[0], "coin": "BTC", "kind": "trade", "size": 1, "px": 100, "fee": 1, "funding": 0, "realized_pnl": 0,
         "position_after": 1},
        {"t": t[1], "coin": "BTC", "kind": "funding", "size": 0, "px": None, "fee": 0, "funding": 2, "realized_pnl": 0,
         "position_after": 1},
        # flip 1 long -> 1 short: closes +10, fee 2 split half/half
        {"t": t[2], "coin": "BTC", "kind": "trade", "size": 2, "px": 110, "fee": 2, "funding": 0, "realized_pnl": 10,
         "position_after": -1},
        {"t": t[3], "coin": "BTC", "kind": "trade", "size": 1, "px": 120, "fee": 1, "funding": 0, "realized_pnl": -10,
         "position_after": 0},
        {"t": t[4], "coin": "ETH", "kind": "trade", "size": 1, "px": 10, "fee": 0, "funding": 0, "realized_pnl": 0,
         "position_after": 1},
    ]
    cs = cycles(rows)
    assert [(c.coin, c.side, round(c.pnl, 6), c.closed_at is not None) for c in cs] == \
        [("BTC", 1, 6.0, True), ("BTC", -1, -12.0, True), ("ETH", 1, 0.0, False)]
    st = cycle_stats(cs)
    assert st["closed"] == 2 and st["win_rate"] == 0.5 and st["profit_factor"] == 0.5
    assert cycle_stats(cs, since=t[3])["closed"] == 1


def test_period_stats_daily_and_correlation():
    from datetime import date

    from app.sim.analytics import correlation, daily_pnl, period_stats

    eq = [(T0 + timedelta(days=i), v, lv) for i, (v, lv) in enumerate([(100, 1000), (120, 1100), (90, 1100), (110, 1200)])]
    st = period_stats(eq, 100, T0 + timedelta(days=1))  # opens at the day-1 close of 120
    assert st["pnl"] == -10 and st["max_dd"] == pytest.approx(90 / 120 - 1)
    assert st["leader_return"] == pytest.approx(1200 / 1100 - 1)
    assert st["retention"] == pytest.approx((110 / 120 - 1) / (1200 / 1100 - 1))
    days = daily_pnl([(date(2026, 11, 1), 105.0), (date(2026, 11, 2), 99.75)], 100)
    assert [d["pnl"] for d in days] == [5.0, -5.25] and days[1]["return"] == pytest.approx(-0.05)
    a = {f"d{i}": r for i, r in enumerate([0.01, -0.02, 0.03, 0.0, 0.01])}
    b = {k: v * 2 for k, v in a.items()}
    m = correlation({1: a, 2: b, 3: {"d0": 0.1}})["matrix"]
    assert m[0][1] == pytest.approx(1.0) and m[0][2] is None


def test_dashboard_and_detail_analytics(client, sf):
    leader = "0x" + "f" * 40
    sid = client.post("/api/v1/sims", json={"name": "a", "leader": leader, "capital": 1000}).json()["id"]
    with sf() as s:
        wid = s.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": leader}).scalar_one()
        s.execute(text("INSERT INTO live_account (wallet_id, account_value, fetched_at) VALUES (:w, 1000, now())"),
                  {"w": wid})
        s.commit()
    step_simulations(sf, MIDS)  # one equity snapshot
    d = client.get("/api/v1/sims/dashboard", params={"period": "30d"}).json()
    assert d["sims"][0]["period"]["pnl"] == 0 and d["portfolio"]["capital"] == 1000
    assert d["correlation"]["ids"] == [sid]
    a = client.get(f"/api/v1/sims/{sid}").json()["analytics"]
    assert set(a["periods"]) == {"1d", "7d", "30d", "all"} and len(a["daily"]) == 1 and a["cycles"] == []
    assert len(client.get(f"/api/v1/sims/{sid}").json()["equity"]) == 1
    assert client.get("/api/v1/sims/dashboard", params={"period": "2y"}).status_code == 422


def test_proportional_copy_formula():
    """Your size = copy ratio x (leader position / leader balance) x your balance."""
    mids = {"BTC": D(100)}
    leader = {"BTC": D(1)}  # $100 on a $1,000 account = 10%
    full = target_sizes(Params(size_factor=D(1)), leader, D(1000), D(100), mids, {"BTC"}, {})
    double = target_sizes(Params(size_factor=D(2)), leader, D(1000), D(100), mids, {"BTC"}, {})
    half = target_sizes(Params(size_factor=D("0.5")), leader, D(1000), D(100), mids, {"BTC"}, {})
    assert full["BTC"] * 100 == pytest.approx(D(10)) and double["BTC"] * 100 == pytest.approx(D(20))
    assert half["BTC"] * 100 == pytest.approx(D(5))


def set_leader_risk(s, wid, coin, szi, lev, liq):
    s.execute(text("DELETE FROM live_position WHERE wallet_id = :w"), {"w": wid})
    if szi:
        s.execute(text("""INSERT INTO live_position (wallet_id, coin, szi, leverage, liquidation_px, liq_risk, updated_at)
                          VALUES (:w, :c, :z, :l, :q, false, now())"""), {"w": wid, "c": coin, "z": szi, "l": lev, "q": liq})


def test_leader_leverage_isolated_liquidation_and_mae(sf):
    wid, sid = seed(sf, {})
    with sf() as s:
        s.execute(text("UPDATE copy_sim SET leverage_mode = 'leader' WHERE id = :s"), {"s": sid})
        s.commit()
    step_simulations(sf, MIDS, now=T0)
    with sf() as s:
        set_leader_risk(s, wid, "BTC", "2", 10, None)  # 40% of equity at 10x
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    with sf() as s:
        p = s.execute(text("SELECT size, leverage, liq_px, margin FROM sim_position WHERE sim_id = :s"), {"s": sid}).one()
    entry = D(80000) * D("1.0003")
    assert p.leverage == 10 and p.margin == pytest.approx(D("0.05") * entry / 10)
    assert p.liq_px == pytest.approx(entry * (1 - D("0.1") + D(1) / 40))  # mmr = 1 / (2 x 20x)
    step_simulations(sf, {**MIDS, "BTC": D(77000)}, now=T0 + timedelta(seconds=60))  # drawdown, not liquidated
    step_simulations(sf, {**MIDS, "BTC": D(73000)}, now=T0 + timedelta(seconds=90))  # through the isolated liq price
    with sf() as s:
        sim = s.execute(text("SELECT status, cash FROM copy_sim WHERE id = :s"), {"s": sid}).one()
        liq = s.execute(text("""SELECT kind, reason, mae FROM sim_trade WHERE sim_id = :s ORDER BY id DESC LIMIT 1"""),
                        {"s": sid}).one()
    assert sim.status == "running"  # only that position went; the account goes on
    assert liq.kind == "liquidation" and liq.reason == "isolated_liquidation"
    assert liq.mae < D("0.05") * (D(77000) - entry)  # worst point is the liquidation fill, below the 77k mark
    lost = D(10000) - sim.cash  # margin ~$400; liquidated at 1 - mmr x lev = 75% of it, plus fees: never the margin+
    assert D(290) < lost < D(320)


def test_leader_liquidation_is_mirrored(sf):
    wid, sid = seed(sf, {})
    step_simulations(sf, MIDS, now=T0)
    with sf() as s:
        set_leader_risk(s, wid, "BTC", "2", 20, 76500)
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    with sf() as s:
        set_leader_risk(s, wid, "BTC", None, None, None)  # position gone with the price at the liquidation level
        s.commit()
    step_simulations(sf, {**MIDS, "BTC": D(76400)}, now=T0 + timedelta(seconds=60))
    assert pos(sf, sid) == {}
    with sf() as s:
        last = s.execute(text("SELECT kind, reason FROM sim_trade WHERE sim_id = :s ORDER BY id DESC LIMIT 1"),
                         {"s": sid}).one()
    assert last.reason == "leader_liquidated" and last.kind == "trade"  # account mode: a close, labelled


def test_capped_cycles_are_counted(sf):
    wid, sid = seed(sf, {})  # account mode, 10x cap
    step_simulations(sf, MIDS, now=T0)
    with sf() as s:
        set_leader(s, wid, {"BTC": "100"})  # $8M on a $400k leader = 20x -> capped at 10x
        s.commit()
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=30))
    with sf() as s:
        st = s.execute(text("SELECT state FROM copy_sim WHERE id = :s"), {"s": sid}).scalar_one()
        s.execute(text("UPDATE copy_sim SET max_leverage = 125 WHERE id = :s"), {"s": sid})  # follow: no cap
        s.commit()
    assert st["sized_cycles"] == 1 and st["capped_cycles"] == 1
    step_simulations(sf, MIDS, now=T0 + timedelta(seconds=60))
    assert pos(sf, sid)["BTC"] * MIDS["BTC"] == pytest.approx(D(200000), rel=D("0.01"))  # the leader's 20x
