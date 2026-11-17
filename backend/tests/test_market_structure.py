from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.alerts.diff import Pos
from app.db import get_session
from app.main import app
from app.market.observe import record_observation
from app.market.structure import Row, build, find_zones, histogram

REF = 100.0


def R(side, ntl, entry=None, liq=None, lev=None, mode="cross", wid=1) -> Row:
    return Row(wid, side, float(ntl), entry, liq, lev, mode)


def test_histogram_and_zones():
    rows = [R(1, 100, entry=e) for e in (90.1, 90.2, 90.3, 91.0)] + [R(1, 50, entry=110.5), R(1, 5, entry=130.0),
                                                                      R(1, 999, entry=200.0)]
    bins, outside = histogram([(r.entry, r) for r in rows], REF, 0.4, 80)
    assert outside == {"positions": 1, "notional": 999.0}  # 200 is beyond 100 x 1.4
    total = sum(b["ntl"] for b in bins)
    zones = find_zones(bins, REF, total)
    # the 90-91 cluster (two neighbouring bins), then 110.5; the lone 130 (1% of the total) is below the 5% floor
    assert len(zones) == 2
    assert zones[0]["notional"] == 400 and zones[0]["positions"] == 4 and zones[0]["lo"] <= 90.1 and zones[0]["hi"] > 91
    assert zones[0]["avg_px"] == pytest.approx((90.1 + 90.2 + 90.3 + 91.0) / 4) and zones[0]["distance"] < 0
    assert zones[1]["notional"] == 50 and zones[1]["share"] == pytest.approx(50 / 455)


def test_entries_cost_basis_and_underwater():
    rows = [R(1, 300, entry=90, wid=1), R(1, 100, entry=110, wid=2),  # longs: 75% in profit at 100
            R(-1, 200, entry=120, wid=3), R(-1, 200, entry=95, wid=4)]  # shorts: entry 120 profits, 95 is underwater
    s = build(rows, REF)
    lg, sh = s["entries"]["long"], s["entries"]["short"]
    assert lg["avg_entry"] == pytest.approx(95) and lg["profit_share"] == pytest.approx(0.75)
    assert lg["underwater_share"] == pytest.approx(0.25) and lg["top5_share"] == 1.0
    assert sh["profit_share"] == pytest.approx(0.5) and sh["avg_distance"] == pytest.approx(0.075)
    assert {z["in_profit"] for z in lg["zones"]} == {True, False}


def test_liquidations_nearest_ladder_and_filters():
    rows = [
        R(1, 1000, liq=97, lev=25), R(1, 50, liq=99.5, lev=50), R(1, 500, liq=85, lev=5), R(1, 200, lev=3),
        R(1, 70, liq=101, lev=40),  # a long with a liquidation price above the price: stale, kept apart
        R(-1, 800, liq=104, lev=20, mode="isolated"), R(-1, 300, liq=150, lev=10),
    ]
    s = build(rows, REF, liq_span=0.3, bins=60)
    lg, sh = s["liquidations"]["long"], s["liquidations"]["short"]
    # nearest single long is the small 99.5 one; the nearest significant cluster is still that bin (50 >= 2% of 1550)
    assert lg["nearest"]["px"] == 99.5 and lg["nearest"]["distance"] == pytest.approx(-0.005)
    assert lg["crossed"] == {"positions": 1, "notional": 70.0}
    assert lg["without_liq"] == {"positions": 1, "notional": 200.0}
    assert lg["beyond_notional"] == 1550 and lg["known_share"] == pytest.approx(1620 / 1820)
    moves = {x["move"]: x["notional"] for x in lg["ladder"]}
    assert moves[-1] == 50 and moves[-3] == 1050 and moves[-15] == 1550
    assert all(a["notional"] <= b["notional"] for a, b in zip(lg["ladder"], lg["ladder"][1:]))
    assert sh["nearest"]["px"] == 104 and sh["nearest"]["mode"] == "isolated"
    assert sh["outside"] == {"positions": 1, "notional": 300.0}  # 150 is beyond +30%
    assert {x["move"]: x["notional"] for x in sh["ladder"]}[5] == 800
    # leverage filter applies to liquidations only
    hi = build(rows, REF, min_lev=20)
    assert hi["liquidations"]["long"]["beyond_notional"] == 1050 and hi["entries"]["long"]["positions"] == 5


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _wallet(s, addr):
    return s.execute(text("""
        INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at, present_in_latest, tier)
        VALUES (:a, now(), 't', now(), true, 'B') RETURNING id"""), {"a": addr}).scalar_one()


def test_structure_api_and_zone_drilldown(client, sf):
    now = datetime.now(UTC)
    with sf() as s:
        for i, (szi, entry, liq, lev) in enumerate([(1, 60000, 55000, 20), (2, 60500, 56000, 20),
                                                    (1, 70000, None, 3), (-1, 66000, 70000, 25)]):
            w = _wallet(s, "0x" + f"{i:x}" * 40)
            record_observation(s, w, "hyperliquid", {"BTC": Pos(D(szi), D(entry), lev, D(liq) if liq else None,
                                                                abs(D(szi)) * D(65000), D(0), "cross")}, D(1e6),
                               now - timedelta(minutes=i))
        s.commit()
    st = client.get("/api/v1/market/structure", params={"coin": "BTC", "bins": 40}).json()
    assert st["ref"]["px"] == 65000 and st["positions"] == 4
    longs = st["entries"]["long"]
    assert longs["notional"] == 260000 and longs["profit_share"] == pytest.approx(195000 / 260000)
    z = longs["zones"][0]
    rows = client.get("/api/v1/market/contributors", params={"coin": "BTC", "side": "long", "entry_min": z["lo"],
                                                             "entry_max": z["hi"]}).json()["rows"]
    assert sum(r["notional_usd"] for r in rows) == pytest.approx(z["notional"])
    near = st["liquidations"]["long"]["nearest"]
    assert near["px"] == 56000 and near["address"] == "0x" + "1" * 40
    assert st["liquidations"]["short"]["nearest"]["px"] == 70000
    coins = {r["coin"]: r for r in client.get("/api/v1/market/coins", params={"min_traders": 1}).json()["rows"]}
    assert coins["BTC"]["near_long_liq_pct"] == pytest.approx((65000 - 56000) / 65000)
    assert coins["BTC"]["near_short_liq_pct"] == pytest.approx(5000 / 65000)
    assert client.get("/api/v1/market/structure", params={"coin": "NOPE"}).json()["ref"] is None
