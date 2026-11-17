import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import app
from app.research.holders import capture_for_leg, position_path, refine_pivot
from app.research.timing import Series

T0 = datetime(2026, 7, 1, tzinfo=UTC)


def test_position_path_uses_last_decision_before_each_hour():
    decisions = [(T0 + timedelta(hours=2, minutes=10), 0.0, 1.0), (T0 + timedelta(hours=5), 1.0, 3.0)]
    hours = np.arange(int(T0.timestamp() // 3600), int(T0.timestamp() // 3600) + 7)
    assert position_path(decisions, hours).tolist() == [0, 0, 0, 1, 1, 3, 3]


def test_capture_full_hold_and_wrong_side():
    prices = np.linspace(100, 150, 11)  # +50% leg
    full = capture_for_leg(np.full(11, 2.0), prices, "up", 0.5)
    # held over hours 0..9; the peak notional is at the start of the last held hour (price 145)
    assert full["capture_ratio"] == pytest.approx(100 / (2 * 145 * 0.5), rel=1e-6)
    assert full["time_aligned"] == 1.0 and full["pnl_mark"] == pytest.approx(100)
    short = capture_for_leg(np.full(11, -1.0), prices, "up", 0.5)
    assert short["time_against"] == 1.0 and short["pnl_mark"] < 0 and short["capture_ratio"] < 0


def test_refine_pivot_finds_hour_of_extreme():
    t0m = int(T0.timestamp() // 60)
    highs = np.full(48, 100.0)
    highs[13] = 120.0
    lows = np.full(48, 90.0)
    lows[5] = 80.0
    s = Series(t0m, 60, highs, lows, highs, highs, lows)
    assert refine_pivot(s, T0, "H") == T0 + timedelta(hours=13)
    assert refine_pivot(s, T0, "L") == T0 + timedelta(hours=5)


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed_cycles(sf):
    """A: long 65k, exits 85k (80%) -> qualifies. B: long 64k, exits at 66k -> no. C: short 86k, still open at 64k."""
    with sf() as s:
        ids = {}
        for n in "abc":
            ids[n] = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
                present_in_latest, account_value) VALUES (:a, now(), 't', now(), true, 100000) RETURNING id"""),
                {"a": "0x" + n * 40}).scalar_one()
            s.execute(text("""INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at)
                VALUES (:w, 't', 1, '2026-07-01', 'synced', 5, now())"""), {"w": ids[n]})

        def cycle(w, direction, status, entry, size, entries, exits, opened="2026-07-10", closed=None):
            s.execute(text("""INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status,
                complete, scale, holding_hours, entry_px, exit_px, max_size, max_notional, n_fills, n_decisions,
                realized_pnl, fees, entries, exits)
                VALUES (:w, 'BTC', 'p2', :d, :o, :c, :st, true, 'macro', 1500, :e, NULL, :sz, :sz * :e, 2, 2, 0, 0,
                        CAST(:en AS jsonb), CAST(:ex AS jsonb))"""),
                {"w": w, "d": direction, "o": opened, "c": closed, "st": status, "e": entry, "sz": size,
                 "en": json.dumps(entries), "ex": json.dumps(exits)})

        cycle(ids["a"], "long", "closed", 65000, 1.0, [["2026-07-10T00:00:00+00:00", 65000, 1.0]],
              [["2026-09-20T00:00:00+00:00", 85000, 0.8], ["2026-09-25T00:00:00+00:00", 80000, 0.2]], closed="2026-09-25")
        cycle(ids["b"], "long", "closed", 64000, 1.0, [["2026-07-10T00:00:00+00:00", 64000, 1.0]],
              [["2026-07-12T00:00:00+00:00", 66000, 1.0]], closed="2026-07-12")
        cycle(ids["c"], "short", "open", 86000, 2.0, [["2026-09-22T00:00:00+00:00", 86000, 2.0]], [], opened="2026-09-22")
        s.execute(text("""INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, n, fetched_at)
            VALUES ('hyperliquid', 'BTC', '1h', '2026-11-15', 64000, 64000, 64000, 64000, 1, 1, now())"""))
        s.commit()


def test_zone_holders_presets(client, sf):
    seed_cycles(sf)
    long = client.get("/api/v1/holders/zones", params={"preset": "btc_long_hold"}).json()
    assert long["qualifying"] == 1 and long["rows"][0]["address"] == "0x" + "a" * 40
    row = long["rows"][0]
    assert row["exited_in_zone_share"] == pytest.approx(0.8) and row["zone_exit_px"] == pytest.approx(85000)
    assert row["move_captured"] == pytest.approx(20000 / 65000)
    near = client.get("/api/v1/holders/zones", params={"preset": "btc_long_hold", "include_near": True}).json()
    assert {r["address"][2] for r in near["rows"]} == {"a", "b"}

    short = client.get("/api/v1/holders/zones", params={"preset": "btc_short_hold"}).json()
    assert short["qualifying"] == 1
    r = short["rows"][0]
    assert r["status"] == "open" and r["holding_share"] == 1.0 and r["unrealized"] == pytest.approx(2 * 22000)


def test_zone_holders_tolerate_json_null_exits(client, sf):
    """Open cycles written before migration 0012 hold JSON null exits; the query must still treat them as open."""
    seed_cycles(sf)
    with sf() as s:
        s.execute(text("UPDATE position_cycle SET exits = 'null'::jsonb WHERE status = 'open'"))
        s.commit()
    res = client.get("/api/v1/holders/zones", params={"preset": "btc_short_hold"})
    assert res.status_code == 200
    r = res.json()["rows"][0]
    assert r["status"] == "open" and r["holding_share"] == 1.0


def test_holders_validation_and_empty_capture(client, sf):
    seed_cycles(sf)
    assert client.get("/api/v1/holders/zones", params={"preset": "x"}).status_code == 422
    assert client.get("/api/v1/holders/zones", params={"entry_min": 5, "entry_max": 1}).status_code == 422
    assert client.get("/api/v1/holders/capture", params={"coin": "BTC"}).json()["rows"] == []
    assert client.get("/api/v1/timing", params={"group": "coin"}).status_code == 422
    assert client.get(f"/api/v1/wallets/0x{'a' * 40}/replay", params={"interval": "5m"}).status_code == 422
