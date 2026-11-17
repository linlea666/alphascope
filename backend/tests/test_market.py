from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.alerts.diff import Pos
from app.db import get_session
from app.main import app
from app.market.collect import okx_open_times, sync_watch
from app.market.observe import changes, record_observation
from app.market.stats import coin_rows, event_counts, snapshot_hour, totals

T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def P(szi, px=100, lev=None, liq=None, mode="cross", entry=None) -> Pos:
    szi = D(str(szi))
    return Pos(szi, D(str(entry)) if entry else D(str(px)), lev, D(str(liq)) if liq else None, abs(szi) * D(str(px)),
               D(0), mode)


def test_changes_kinds_and_time_precision():
    prev = {"BTC": {"szi": D(1), "mark_px": D(100)}, "ETH": {"szi": D(2), "mark_px": D(10)},
            "SOL": {"szi": D(-3), "mark_px": D(5)}, "XRP": {"szi": D(4), "mark_px": D(1)}}
    after = {"BTC": P(2), "ETH": P(-1, 10), "SOL": P(-1, 5), "DOGE": P(10, 1)}
    src = T0 - timedelta(minutes=10)
    out = {c.coin: c for c in changes(prev, after, {"DOGE": src, "ETH": src}, {"XRP": D("1.2")},
                                      T0 - timedelta(minutes=30), T0)}
    assert {c: x.kind for c, x in out.items()} == {"BTC": "increase", "ETH": "flip", "SOL": "decrease",
                                                   "XRP": "close", "DOGE": "open"}
    # the venue-reported open time inside the window makes opens and flips exact; other changes are windows
    assert (out["DOGE"].time_precision, out["DOGE"].event_time) == ("exact", src)
    assert out["ETH"].time_precision == "exact" and out["BTC"].time_precision == "window"
    assert out["BTC"].window_start == T0 - timedelta(minutes=30) and out["BTC"].event_time == T0
    assert out["XRP"].side == "long" and out["XRP"].notional_change == D("4.8")  # closed at the current mid
    assert out["SOL"].side == "short" and out["SOL"].notional_change == D(10)
    # a source time before the previous poll is not this window's open: it stays a window
    old = changes({}, {"DOGE": P(10, 1)}, {"DOGE": T0 - timedelta(hours=5)}, {}, T0 - timedelta(minutes=30), T0)
    assert old[0].kind == "open" and old[0].time_precision == "window"
    # first poll: what is open was not opened now
    first = changes({}, {"BTC": P(1)}, {}, {}, None, T0)
    assert first[0].kind == "first_seen" and first[0].time_precision == "window" and first[0].window_start is None


def test_okx_open_times_use_the_net_side():
    ms = lambda t: str(int(t.timestamp() * 1000))  # noqa: E731
    raw = [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "3", "cTime": ms(T0 - timedelta(hours=2))},
           {"instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "1", "cTime": ms(T0 - timedelta(hours=9))},
           {"instId": "ETH-USDT-SWAP", "posSide": "net", "pos": "-5", "cTime": ms(T0 - timedelta(hours=1))}]
    assert okx_open_times(raw) == {"BTC": T0 - timedelta(hours=2), "ETH": T0 - timedelta(hours=1)}


def _wallet(s, addr, venue="hyperliquid"):
    return s.execute(text("""
        INSERT INTO wallet (address, venue, first_seen_at, first_seen_source, last_seen_at, present_in_latest, tier)
        VALUES (:a, :v, now(), 't', now(), :p, :tier) RETURNING id"""),
        {"a": addr, "v": venue, "p": venue == "hyperliquid", "tier": "B" if venue == "hyperliquid" else None}
    ).scalar_one()


def test_record_observation_rows_events_and_schedule(sf):
    with sf() as s:
        wid = _wallet(s, "0x" + "a" * 40)
        first = record_observation(s, wid, "hyperliquid", {"BTC": P(1, 100, 10, 90)}, D(1000), T0)
        assert [c.kind for c in first] == ["first_seen"]
        t1 = T0 + timedelta(minutes=30)
        record_observation(s, wid, "hyperliquid", {"BTC": P(2, 110, 10, 95), "ETH": P(-5, 10, 25)}, D(1000), t1)
        again = record_observation(s, wid, "hyperliquid", {"BTC": P(2, 110, 10, 95), "ETH": P(-5, 10, 25)}, D(1000),
                                   t1 + timedelta(minutes=30))
        assert again == []
        s.commit()
        rows = {r.coin: r for r in s.execute(text("SELECT * FROM trader_position")).all()}
        assert rows["BTC"].first_seen_at == T0 and rows["BTC"].opened_at_source == "unknown"  # seen open at first
        assert rows["ETH"].opened_at_source == "window" and rows["ETH"].opened_after == T0
        assert rows["ETH"].notional_usd == D(50) and rows["BTC"].liq_px == D(95)
        assert s.execute(text("SELECT kind FROM market_event ORDER BY id")).scalars().all() == \
            ["first_seen", "increase", "open"]
        w = s.execute(text("SELECT tier, next_due, stale_after FROM position_watch")).one()
        # $270 gross < hot threshold: warm (hourly), stale after two missed polls
        assert w.tier == "warm" and w.next_due == t1 + timedelta(minutes=90) and \
            w.stale_after == t1 + timedelta(minutes=150)


def _seed_market(sf):
    """Three fresh traders and one stale one; trader B is in the watchlist."""
    with sf() as s:
        a = _wallet(s, "0x" + "a" * 40)
        b = _wallet(s, "0x" + "b" * 40)
        c = _wallet(s, "okx:1000001", "okx")
        stale = _wallet(s, "0x" + "d" * 40)
        g = s.execute(text("INSERT INTO watch_group (owner, name) VALUES ('default', 'g') RETURNING id")).scalar_one()
        s.execute(text("INSERT INTO watch_item (group_id, wallet_id) VALUES (:g, :w)"), {"g": g, "w": b})
        now = datetime.now(UTC)
        record_observation(s, a, "hyperliquid", {"BTC": P(1, 100, 10, 80)}, D(1000), now - timedelta(minutes=5))
        record_observation(s, b, "hyperliquid", {"BTC": P(-2, 100, 25, 104), "ETH": P(30, 10, 5)}, D(1000),
                           now - timedelta(minutes=5))
        record_observation(s, c, "okx", {"BTC": P(3, 100, 20), "ETH": P(-50, 10, 3)}, None,
                           now - timedelta(minutes=5))
        record_observation(s, stale, "hyperliquid", {"BTC": P(100, 100)}, D(1000), now - timedelta(days=1))
        s.commit()
        return {"a": a, "b": b, "c": c, "stale": stale}


def test_aggregates_counting_rules_and_cohorts(sf):
    _seed_market(sf)
    now = datetime.now(UTC)
    with sf() as s:
        btc = {r["coin"]: r for r in coin_rows(s, "all", "all", now)}["BTC"]
        assert (btc["long_traders"], btc["short_traders"]) == (2, 1)  # the stale 100 BTC long is not counted
        assert (btc["long_ntl"], btc["short_ntl"]) == (D(400), D(200))
        assert btc["lev20_long_ntl"] == D(300) and btc["lev20_short_ntl"] == D(200) and btc["lev10_long_ntl"] == D(400)
        assert btc["liq_known_ntl"] == D(300)  # only positions with a reported liquidation price
        t = totals(s, "all", "all", now)
        # per trader net: A +100, B -200+300 = +100, C +300-500 = -200
        assert (t["traders"], t["long_traders"], t["short_traders"], t["positions"]) == (3, 2, 1, 5)
        okx = totals(s, "okx", "all", now)
        assert (okx["traders"], okx["long_ntl"], okx["short_ntl"]) == (1, D(300), D(500))
        mine = {r["coin"]: r for r in coin_rows(s, "all", "watchlist", now)}
        assert set(mine) == {"BTC", "ETH"} and mine["BTC"]["short_traders"] == 1 and mine["BTC"]["long_traders"] == 0
        # first sightings are never opens
        assert all(e == {"opens": 0, "closes": 0} for e in event_counts(s, "all", "all", now - timedelta(days=2),
                                                                         now).values())
        n1 = snapshot_hour(s, now)
        n2 = snapshot_hour(s, now)
        s.commit()
        assert n1 == n2 and s.execute(text("SELECT count(*) FROM market_snapshot")).scalar_one() == n1
        star = s.execute(text("""SELECT observed, long_traders, short_traders FROM market_snapshot
                                 WHERE venue = 'all' AND cohort = 'all' AND coin = '*'""")).one()
        assert tuple(star) == (3, 2, 1)


def test_sync_watch_membership(sf):
    ids = _seed_market(sf)
    with sf() as s:
        s.execute(text("UPDATE wallet SET tier = 'C' WHERE id = :w"), {"w": ids["a"]})  # left tier B
        s.execute(text("UPDATE wallet SET handle = 'H1' WHERE id = :w"), {"w": ids["c"]})
        s.execute(text("""INSERT INTO okx_trader (wallet_id, history_status, pool_status, priority, target_days,
                          n_positions, failure_count, updated_at) VALUES (:w, 'partial', 'active', 1, 365, 0, 0, now())"""),
                  {"w": ids["c"]})
        r = sync_watch(s, datetime.now(UTC))
        s.commit()
        assert r["removed"] == 1 and r["watched"] == 3
        assert s.execute(text("SELECT count(*) FROM trader_position WHERE wallet_id = :w"), {"w": ids["a"]}).scalar() == 0
        bases = dict(s.execute(text("SELECT wallet_id, base_tier FROM position_watch")).all())
        assert bases == {ids["b"]: "hot", ids["c"]: "hot", ids["stale"]: "cold"}  # watched / on board / plain B


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_drilldown_matches_aggregates_and_liquidations(client, sf):
    _seed_market(sf)
    coins = {r["coin"]: r for r in client.get("/api/v1/market/coins", params={"min_traders": 1}).json()["rows"]}
    longs = client.get("/api/v1/market/contributors", params={"coin": "BTC", "side": "long"}).json()["rows"]
    assert len(longs) == coins["BTC"]["long_traders"] and sum(r["notional_usd"] for r in longs) == coins["BTC"]["long_ntl"]
    ov = client.get("/api/v1/market/overview").json()
    assert ov["coverage"]["watched"] == 4 and ov["coverage"]["fresh"] == 3 and ov["totals"]["positions"] == 5
    liq = client.get("/api/v1/market/liquidations", params={"coin": "BTC", "span_pct": 30, "bins": 30}).json()
    placed = sum(b["long_ntl"] + b["short_ntl"] for b in liq["bins"])
    assert placed == 300 and liq["without_liq"] == {"positions": 1, "notional": 300.0}
    assert sum(b["long_traders"] for b in liq["bins"]) == 1 and sum(b["short_traders"] for b in liq["bins"]) == 1
    ev = client.get("/api/v1/market/events", params={"kinds": "first_seen"}).json()["rows"]
    assert len(ev) == 5  # fresh traders' first sightings (the stale one's is older than 24h)
    assert client.get("/api/v1/market/events", params={"kinds": "bogus"}).status_code == 422
