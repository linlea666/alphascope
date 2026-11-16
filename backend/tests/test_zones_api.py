from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import app


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed(sf):
    """Wallet A: long BTC at 63k in Aug, closed at 85k; short at 86k in Sep, still open. Wallet B: long at 75k."""
    with sf() as s:
        ids = {}
        for name in ("a", "b"):
            ids[name] = s.execute(text("""
                INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at, present_in_latest, account_value)
                VALUES (:a, now(), 't', now(), true, 50000) RETURNING id"""), {"a": "0x" + name * 40}).scalar_one()
            s.execute(text("""INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills,
                              queued_at, earliest_fill_at) VALUES (:w, 'test', 1, '2026-07-01', 'synced', 10, now(),
                              '2026-07-02')"""), {"w": ids[name]})

        def cycle(w, direction, opened, closed, status, entry, exit_, net):
            return s.execute(text("""
                INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
                  scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions, realized_pnl, fees)
                VALUES (:w, 'BTC', 'p1', :d, :o, :c, :st, true, 'macro', 500, :e, :x, 1, 1, 1, 1, :net, 0)
                RETURNING id"""), {"w": w, "d": direction, "o": opened, "c": closed, "st": status, "e": entry,
                                   "x": exit_, "net": net}).scalar_one()

        def decision(w, cid, kind, side, t, px, before, after):
            s.execute(text("""
                INSERT INTO trade_decision (wallet_id, cycle_id, coin, version, kind, side, started_at, ended_at, n_fills,
                  size, notional, vwap, pos_before, pos_after, realized_pnl, fee)
                VALUES (:w, :c, 'BTC', 'p1', :k, :sd, :t, :t, 1, 1, :px, :px, :b, :a, 0, 0)"""),
                {"w": w, "c": cid, "k": kind, "sd": side, "t": t, "px": px, "b": before, "a": after})

        c1 = cycle(ids["a"], "long", datetime(2026, 8, 5, tzinfo=UTC), datetime(2026, 9, 20, tzinfo=UTC), "closed",
                   63000, 85000, 22000)
        decision(ids["a"], c1, "open", "buy", datetime(2026, 8, 5, tzinfo=UTC), 63000, 0, 1)
        decision(ids["a"], c1, "close", "sell", datetime(2026, 9, 20, tzinfo=UTC), 85000, 1, 0)
        c2 = cycle(ids["a"], "short", datetime(2026, 9, 25, tzinfo=UTC), None, "open", 86000, None, 0)
        decision(ids["a"], c2, "open", "sell", datetime(2026, 9, 25, tzinfo=UTC), 86000, 0, -1)
        c3 = cycle(ids["b"], "long", datetime(2026, 9, 1, tzinfo=UTC), None, "open", 75000, None, 0)
        decision(ids["b"], c3, "open", "buy", datetime(2026, 9, 1, tzinfo=UTC), 75000, 0, 1)
        s.execute(text("""INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
                          VALUES ('hyperliquid', 'BTC', '1h', '2026-11-15', 62000, 62000, 62000, 62000, 1, 1, now())"""))
        s.commit()


def test_presets_and_intersection(client, sf):
    seed(sf)
    low = client.get("/api/v1/zones", params={"preset": "btc_low_long"}).json()
    assert [r["address"][:3] for r in low["rows"]] == ["0xa"]  # B bought at 75k, outside the zone
    z = low["rows"][0]["zone"]
    assert z["avg_px"] == 63000 and z["closed"] == 1 and z["realized_net"] == 22000 and z["avg_exit_px"] == 85000

    high = client.get("/api/v1/zones", params={"preset": "btc_high_short"}).json()
    short = high["rows"][0]["zone"]
    assert short["open"] == 1 and short["unrealized"] == pytest.approx(24000)  # -1 BTC * (62k - 86k)

    both = client.get("/api/v1/zones", params={"preset": "btc_both"}).json()
    assert both["total"] == 1 and "also" in both["rows"][0]
    assert both["coverage"]["archived"] == 2


def test_custom_query_validation(client, sf):
    seed(sf)
    assert client.get("/api/v1/zones", params={"preset": "nope"}).status_code == 422
    r = client.get("/api/v1/zones", params={"coin": "BTC", "side": "long", "px_min": 70000, "px_max": 80000,
                                            "start": "2026-08-01", "end": "2026-09-30"}).json()
    assert [x["address"][:3] for x in r["rows"]] == ["0xb"]
    assert client.get("/api/v1/zones", params={"px_min": 10, "px_max": 5}).status_code == 422


def test_timing_board_and_wallet_endpoints(client, sf):
    seed(sf)
    assert client.get("/api/v1/timing", params={"scale": "swing", "window": "90d"}).json()["total"] == 0
    assert client.get("/api/v1/timing", params={"sort": "bogus"}).status_code == 422
    w = client.get(f"/api/v1/wallets/0x{'a' * 40}/timing").json()
    assert w["scores"] == [] and w["calendar"] == []
