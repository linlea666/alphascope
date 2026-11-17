from datetime import UTC, datetime

from sqlalchemy import text

from app.alerts import digest, sentinel
from tests.test_zones_api import client, seed  # noqa: F401  (fixture re-export)

A = "0x" + "a" * 40


def test_highlights_compare_and_candles(client, sf):  # noqa: F811
    seed(sf)
    h = client.get(f"/api/v1/wallets/{A}/highlights").json()
    assert h["closed"] == 1 and h["wins"] == 1 and h["best"][0]["net"] == 22000
    assert h["best"][0]["move"] == (85000 - 63000) / 63000 and h["worst"] == []
    cmp = client.get("/api/v1/compare", params={"addr": [A, "0x" + "b" * 40], "days": 730}).json()
    assert [t["address"] for t in cmp["traders"]] == [A, "0x" + "b" * 40]
    assert cmp["traders"][0]["curve"][-1][1] == 22000 and cmp["correlation"][0][0] == 1.0
    assert cmp["correlation"][0][1] is None  # fewer than 10 days: no correlation claimed
    c = client.get("/api/v1/candles", params={"coin": "BTC", "interval": "4h", "since": "2026-11-01"}).json()
    assert c["source"] == "hyperliquid" and c["rows"][0][4] == 62000
    assert client.get("/api/v1/wallets/nope/highlights").status_code == 422


def test_follow_backtest_uses_flow_adjusted_index(client, sf):  # noqa: F811
    seed(sf)
    with sf() as s:
        wid = s.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": A}).scalar_one()
        # +10% then a 50k deposit (not a return) then +10% again
        for ts, av, pnl in (("2026-09-01", 100000, 0), ("2026-09-02", 110000, 10000), ("2026-09-03", 160000, 10000),
                            ("2026-09-04", 176000, 26000)):
            s.execute(text("""INSERT INTO equity_point (wallet_id, series, ts, account_value, pnl, fetched_at)
                VALUES (:w, 'allTime', :t, :av, :p, now())"""), {"w": wid, "t": ts, "av": av, "p": pnl})
        s.commit()
    f = client.get(f"/api/v1/wallets/{A}/follow", params={"since": "2026-09-01", "capital": 1000}).json()
    assert f["supported"] and round(f["final"], 2) == 1210.0 and f["mdd"] == 0


def test_sentinel_reports_new_traders_once(sf):
    seed(sf)
    sent = []
    with sf() as s:
        s.execute(text("""INSERT INTO zone_sentinel (name, coin, side, px_min, px_max, created_at)
                          VALUES ('抄底', 'BTC', 'long', 60000, 76000, '2026-08-01')"""))
        s.execute(text("""INSERT INTO app_setting (key, value, secret) VALUES ('smtp',
            '{"host": "h", "username": "u", "recipients": ["x@y.z"]}', NULL)"""))
        s.commit()
    r = sentinel.run(sf, datetime(2026, 11, 17, tzinfo=UTC), sender=lambda *a, **k: sent.append(a))
    assert r["hits"] == 2 and r["skipped"] == 1  # no SMTP password: recorded, not emailed
    again = sentinel.run(sf, datetime(2026, 11, 17, 1, tzinfo=UTC), sender=lambda *a, **k: sent.append(a))
    assert again["hits"] == 0 and not sent
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM sentinel_hit WHERE notify_status = 'skipped'")).scalar_one() == 2


def test_digest_marks_new_significant_against_previous_day(sf):
    seed(sf)
    first = digest.run(sf, datetime(2026, 11, 16, tzinfo=UTC), email=False)
    assert first["new_significant"] == 0
    with sf() as s:
        wid = s.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": A}).scalar_one()
        s.execute(text("""INSERT INTO timing_score (wallet_id, scale, time_window, coin_group, version, as_of, status,
            n_decisions, n_scored, pivot_hits, pivots, pivots_hit, cycles, q_value)
            VALUES (:w, 'swing', '90d', 'all', 't1', now(), 'scored', 10, 10, 0, 0, 0, 1, 0.01)"""), {"w": wid})
        s.commit()
    second = digest.run(sf, datetime(2026, 11, 17, tzinfo=UTC), email=False)
    assert second["new_significant"] == 1
    with sf() as s:
        p = s.execute(text("SELECT payload FROM daily_digest WHERE day = '2026-11-17'")).scalar_one()
    assert p["new_significant"][0]["address"] == A and not p["baseline"]
