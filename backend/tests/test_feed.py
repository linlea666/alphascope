from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import get_session
from app.main import app
from tests.conftest import load_fixture

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HL = "0x" + "ab" * 20
OKX = "okx:1234567890"
CONTRACT = load_fixture("feed_v1_contract.json")


def keys(name: str) -> set[str]:
    return set(CONTRACT[name])


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_settings] = lambda: Settings(feed_token=TOKEN, feed_settle_seconds=120)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _wallet(s, addr, venue="hyperliquid"):
    return s.execute(text("""
        INSERT INTO wallet (address, venue, first_seen_at, first_seen_source, last_seen_at, present_in_latest, tier,
                            account_value)
        VALUES (:a, :v, now(), 't', now(), true, 'B', 2500000.5) RETURNING id"""), {"a": addr, "v": venue}).scalar_one()


def _event(s, wid, observed, kind="increase", ntl="1500000.123456", venue="hyperliquid", eid=None):
    return s.execute(text(f"""
        INSERT INTO market_event ({"id, " if eid else ""}wallet_id, venue, coin, kind, side, szi_before, szi_after,
          notional_change, mark_px, entry_px, leverage, liq_px, event_time, time_precision, window_start, observed_at)
        VALUES ({":id, " if eid else ""}:w, :v, 'BTC', :k, 'long', 10, 25, :n, 100000.5, 98000, 20, 91000, :t,
                'window', :ws, :t) RETURNING id"""),
        {"id": eid, "w": wid, "v": venue, "k": kind, "n": ntl, "t": observed,
         "ws": observed - timedelta(minutes=30)}).scalar_one()


def test_disabled_and_auth(sf):
    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        app.dependency_overrides[get_settings] = lambda: Settings(feed_token="")
        assert c.get("/api/v1/feed/meta", headers=AUTH).status_code == 404
        app.dependency_overrides[get_settings] = lambda: Settings(feed_token=TOKEN)
        assert c.get("/api/v1/feed/meta").status_code == 401
        assert c.get("/api/v1/feed/meta", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.get("/api/v1/feed/meta", headers=AUTH)
        assert r.status_code == 200 and keys("meta") <= set(r.json()) and keys("meta.rules") <= set(r.json()["rules"])
        assert r.json()["rules"]["liq_tolerance"] == "0.005"
    finally:
        app.dependency_overrides.clear()


def test_events_cursor_never_skips_unsettled_rows(client, sf):
    now = datetime.now(UTC)
    with sf() as s:
        w = _wallet(s, HL)
        for i in (1, 2, 3):
            _event(s, w, now - timedelta(minutes=30) + timedelta(seconds=i), eid=i)
        _event(s, w, now, eid=4)  # committed late / still settling
        _event(s, w, now - timedelta(minutes=10), eid=5)  # older, but behind the unsettled id 4
        s.commit()
    r = client.get("/api/v1/feed/events", params={"after_id": 0}, headers=AUTH).json()
    assert keys("events") <= set(r) and r["schema"] == "feed.v1"
    assert [x["source_id"] for x in r["rows"]] == [1, 2, 3]
    assert r["next_after_id"] == 3 and r["head_id"] == 5 and r["has_more"] is False
    assert keys("events.row") <= set(r["rows"][0])
    # exact decimal strings, never floats
    assert r["rows"][0]["notional_change"] == "1500000.123456" and r["rows"][0]["mark_px"] == "100000.5"
    assert r["rows"][0]["trader_key"] == HL
    with sf() as s:  # id 4 settles
        s.execute(text("UPDATE market_event SET observed_at = now() - interval '5 minutes' WHERE id = 4"))
        s.commit()
    r2 = client.get("/api/v1/feed/events", params={"after_id": r["next_after_id"]}, headers=AUTH).json()
    assert [x["source_id"] for x in r2["rows"]] == [4, 5] and r2["next_after_id"] == 5


def test_events_filters_move_the_cursor_and_limit_does_not_skip(client, sf):
    old = datetime.now(UTC) - timedelta(hours=1)
    with sf() as s:
        w = _wallet(s, HL)
        _event(s, w, old, kind="first_seen", eid=1)
        _event(s, w, old, kind="increase", ntl="10", eid=2)
        for i in (3, 4, 5):
            _event(s, w, old + timedelta(seconds=i), eid=i)
        s.commit()
    r = client.get("/api/v1/feed/events", params={"after_id": 0, "min_notional": 1000, "limit": 2},
                   headers=AUTH).json()
    assert [x["source_id"] for x in r["rows"]] == [3, 4] and r["next_after_id"] == 4 and r["has_more"] is True
    r = client.get("/api/v1/feed/events", params={"after_id": 4, "min_notional": 1000}, headers=AUTH).json()
    assert [x["source_id"] for x in r["rows"]] == [5] and r["has_more"] is False
    r = client.get("/api/v1/feed/events", params={"after_id": 0, "include_first_seen": True}, headers=AUTH).json()
    assert r["rows"][0]["kind"] == "first_seen"
    r = client.get("/api/v1/feed/events", params={"after_id": 0, "venue": "okx"}, headers=AUTH).json()
    assert r["rows"] == [] and r["next_after_id"] == 5


def test_context_hyperliquid(client, sf):
    now = datetime.now(UTC)
    with sf() as s:
        w = _wallet(s, HL)
        _event(s, w, now - timedelta(hours=2))
        s.execute(text("""
            INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due, last_observed_at,
                                        stale_after, failures, account_value, n_positions, gross_notional)
            VALUES (:w, 'hyperliquid', 'hot', 'hot', now(), now(), now() - interval '3 hours',
                    now() - interval '1 hour', 0, 2500000, 1, 2500000)"""), {"w": w})
        s.execute(text("""
            INSERT INTO trader_position (wallet_id, coin, venue, szi, notional_usd, mark_px, entry_px, leverage,
              margin_mode, liq_px, upl, opened_at_source, opened_after, first_seen_at, observed_at)
            VALUES (:w, 'BTC', 'hyperliquid', 25, 2500012.5, 100000.5, 98000, 20, 'cross', 91000, 62512.5,
                    'window', now() - interval '4 hours', now() - interval '3 hours', now() - interval '3 hours')"""),
            {"w": w})
        s.execute(text("""
            INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
              scale, holding_hours, max_size, max_notional, n_fills, n_decisions, realized_pnl, fees)
            VALUES (:w, 'BTC', 'p1', 'long', '2026-10-01', '2026-10-05', 'closed', true, 'swing', 96, 10, 1000000,
                    4, 2, 12345.67, 120.5),
                   (:w, 'ETH', 'p1', 'short', '2026-07-01', NULL, 'open', false, 'macro', 0, 1, 3000, 1, 1, 0, 0)"""),
            {"w": w})
        s.execute(text("""
            INSERT INTO fill (wallet_id, tid, side, time, coin, px, sz, dir, closed_pnl, liquidation)
            VALUES (:w, 1, 'A', now() - interval '1 day', 'BTC', 90000, 1, 'Close Long', -5000,
                    CAST(:liq AS jsonb)),
                   (:w, 2, 'B', now() - interval '1 day', 'BTC', 90000, 1, 'Open Long', 0, NULL)"""),
            {"w": w, "liq": f'{{"liquidatedUser": "{HL.upper().replace("0X", "0x")}", "markPx": "90000"}}'})
        s.execute(text("""
            INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at,
                                   earliest_fill_at, truncated)
            VALUES (:w, 'watchlist', 1, '2026-07-01', 'synced', 2, now(), '2026-07-01', false)"""), {"w": w})
        s.execute(text("""
            INSERT INTO wallet_metric (wallet_id, metric_window, metric_version, computed_at, source_series, covered,
                                       n_points, n_valid_periods, reliability, twr, mdd)
            VALUES (:w, '90d', 'r1', now(), 'allTime', true, 40, 30, 'medium', 0.25, -0.1)"""), {"w": w})
        s.commit()
    r = client.get(f"/api/v1/feed/traders/{HL.upper().replace('0X', '0x')}/context", headers=AUTH)
    assert r.status_code == 200
    c = r.json()
    assert keys("context") <= set(c) and keys("context.trader") <= set(c["trader"])
    assert keys("context.watch") <= set(c["watch"]) and c["watch"]["is_fresh"] is False  # stale poll
    p = c["positions"][0]
    assert keys("context.position") <= set(p) and p["opened_at"] is None and p["opened_at_source"] == "window"
    assert p["notional_usd"] == "2500012.5" and p["implied_margin"].startswith("125000.6")
    assert keys("context.recent_event") <= set(c["recent_events"][0])
    assert keys("context.metric") <= set(c["metrics"][0]) and c["metrics"][0]["reliability"] == "medium"
    assert keys("context.quality") <= set(c["quality"])
    assert keys("context.fill_coverage") <= set(c["fill_coverage"]) and c["fill_coverage"]["status"] == "synced"
    cyc = {x["coin"]: x for x in c["cycles"]}
    assert keys("context.cycle") <= set(cyc["BTC"]) and cyc["BTC"]["complete"] is True and "id" not in cyc["BTC"]
    assert cyc["ETH"]["complete"] is False
    assert len(c["confirmed_liquidations"]) == 1 and keys("context.confirmed_liquidation") <= set(c["confirmed_liquidations"][0])
    assert c["okx"] is None
    assert client.get("/api/v1/feed/traders/0x12/context", headers=AUTH).status_code == 422
    assert client.get(f"/api/v1/feed/traders/0x{'cd' * 20}/context", headers=AUTH).status_code == 404


def test_context_okx(client, sf):
    with sf() as s:
        w = _wallet(s, OKX, venue="okx")
        s.execute(text("""
            INSERT INTO okx_trader (wallet_id, history_status, pool_status, priority, n_positions, target_days,
                                    failure_count, updated_at, oldest_at, newest_at)
            VALUES (:w, 'source_complete', 'active', 1, 2, 365, 0, now(), '2026-01-01', now())"""), {"w": w})
        s.execute(text("""
            INSERT INTO okx_position (wallet_id, pos_id, inst_id, coin, ct_type, direction, open_time, close_time,
              open_avg_px, max_size, closed_size, max_notional, pnl, realized_pnl, fee, funding, close_type,
              liquidated, raw, fetched_at, lever)
            VALUES (:w, 'p1', 'BTC-USDT-SWAP', 'BTC', 'linear', 'long', '2026-11-01', '2026-11-02', 100, 1, 1, 100000,
                    -900, -950, 30, 20, 'liquidateClose', true, '{}', now(), 50)"""), {"w": w})
        s.commit()
    c = client.get(f"/api/v1/feed/traders/{OKX}/context", headers=AUTH).json()
    assert keys("context.okx") <= set(c["okx"]) and keys("context.okx.close") <= set(c["okx"]["closes"][0])
    assert c["okx"]["closes"][0]["liquidated"] is True and c["okx"]["totals"]["liquidations"] == 1
    assert c["fill_coverage"]["source"] == "okx_positions" and c["fill_coverage"]["status"] == "synced"
    assert c["confirmed_liquidations"] == []


def test_at_risk_and_health(client, sf):
    with sf() as s:
        w = _wallet(s, HL)
        s.execute(text("""
            INSERT INTO position_watch (wallet_id, venue, tier, base_tier, added_at, next_due, last_observed_at,
                                        stale_after, failures, account_value, n_positions, gross_notional)
            VALUES (:w, 'hyperliquid', 'hot', 'hot', now(), now(), now(), now() + interval '1 hour', 0, 1e6, 1, 3e6)"""),
            {"w": w})
        s.execute(text("""
            INSERT INTO trader_position (wallet_id, coin, venue, szi, notional_usd, mark_px, entry_px, leverage,
              liq_px, upl, opened_at_source, first_seen_at, observed_at)
            VALUES (:w, 'BTC', 'hyperliquid', 30, 3000000, 100000, 101000, 25, 98000, -30000, 'unknown', now(), now())"""),
            {"w": w})
        s.commit()
    r = client.get("/api/v1/feed/positions/at-risk", headers=AUTH).json()
    assert keys("at_risk") <= set(r) and len(r["rows"]) == 1 and keys("at_risk.row") <= set(r["rows"][0])
    assert r["rows"][0]["liq_dist"].startswith("0.02")
    h = client.get("/api/v1/feed/health", headers=AUTH).json()
    assert keys("health") <= set(h) and keys("health.sources") <= set(h["sources"])
    assert keys("health.radar") <= set(h["sources"]["radar_hyperliquid"])
    assert h["sources"]["radar_hyperliquid"]["status"] == "ok" and h["sources"]["radar_okx"]["status"] == "empty"


def test_feed_is_get_only_and_read_only(client, sf):
    from app.api.feed import feed_session, router
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 12 and all(r.methods == {"GET"} and r.path.startswith("/api/v1/feed/") for r in routes)
    with sf() as s:
        gen = feed_session(s)
        session = next(gen)
        with pytest.raises(Exception, match="read-only"):
            session.execute(text("INSERT INTO ingest_run (kind, status, started_at) VALUES ('x', 'running', now())"))


def test_research_column_endpoints(client, sf):
    from datetime import date
    now = datetime.now(UTC)
    with sf() as s:
        okx = _wallet(s, OKX, venue="okx")
        hl = _wallet(s, HL)
        run = s.execute(text("""INSERT INTO okx_leaderboard_run (business_date, time_window, status, pages_done, unique_count,
                                                                 new_traders, started_at, source)
                                VALUES (:d, '30d', 'complete', 1, 1, 0, now(), 'test') RETURNING id"""),
                        {"d": date.today()}).scalar_one()
        s.execute(text("""INSERT INTO okx_leaderboard_row (run_id, wallet_id, rank, pnl, pnl_ratio)
                          VALUES (:r, :w, 1, 50000, 3.2)"""), {"r": run, "w": okx})
        s.execute(text("""INSERT INTO copy_backtest (wallet_id, window_days, params_key, computed_at, capital, ret, mdd,
                                                     trades, liquidations, costs, n_decisions, approx_equity)
                          VALUES (:w, 30, 'default-v2', now(), 10000, 0.41, -0.2, 12, 0, 5, 24, true)"""), {"w": okx})
        s.execute(text("""INSERT INTO okx_presence (wallet_id, time_window, first_seen_at, last_seen_at, seen_days, streak,
                                                    currently_seen, best_rank, last_rank)
                          VALUES (:w, '7d', now() - interval '20 days', now() - interval '3 days', 15, 0, false, 4, 9)"""),
                  {"w": okx})
        for i, net in enumerate([-50, 100, 200, 300, -400]):  # oldest first: loss, three wins, then the losing close
            s.execute(text("""
                INSERT INTO position_cycle (wallet_id, coin, version, direction, opened_at, closed_at, status, complete,
                  scale, holding_hours, max_size, max_notional, n_fills, n_decisions, realized_pnl, fees)
                VALUES (:w, 'BTC', 'p1', 'long', :o, :c, 'closed', true, 'swing', 30, 1, 100000, 2, 2, :pnl, 10)"""),
                {"w": hl, "o": now - timedelta(days=20 - i * 3),
                 "c": now - timedelta(hours=2) if i == 4 else now - timedelta(days=19 - i * 3), "pnl": net + 10})
        s.commit()
    cb = client.get("/api/v1/feed/copy/board", params={"window": "30d"}, headers=AUTH).json()
    assert keys("copy_board") <= set(cb) and keys("copy_board.row") <= set(cb["rows"][0])
    assert cb["rows"][0]["copy_ret"] == "0.41" and cb["rows"][0]["approx_equity"] is True
    do = client.get("/api/v1/feed/okx/dropouts", headers=AUTH).json()
    assert keys("dropouts") <= set(do) and keys("dropouts.row") <= set(do["rows"][0]) and do["rows"][0]["best_rank"] == 4
    st = client.get("/api/v1/feed/market/structure", params={"coin": "BTC"}, headers=AUTH).json()
    assert keys("structure") <= set(st)
    fc = client.get("/api/v1/feed/research/forecasts", headers=AUTH).json()
    assert keys("forecasts") <= set(fc) and fc["status"] == "shadow"
    fu = client.get("/api/v1/feed/research/funding", headers=AUTH).json()
    assert keys("funding") <= set(fu)
    ho = client.get("/api/v1/feed/cycles/holding", headers=AUTH).json()
    assert keys("holding") <= set(ho) and {r["winner"] for r in ho["rows"]} == {True, False}
    assert keys("holding.row") <= set(ho["rows"][0])
    cl = client.get("/api/v1/feed/cycles/closed", params={"min_streak": 3}, headers=AUTH).json()
    assert keys("closed") <= set(cl) and len(cl["rows"]) == 1
    row = cl["rows"][0]
    assert keys("closed.row") <= set(row) and row["prior_streak"] == 3 and row["prior_known"] is True
    assert row["realized_pnl"] == "-390"
