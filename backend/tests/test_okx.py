import json
import sqlite3
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from app.okx.legacy_import import export_sqlite, import_bundle
from app.okx.positions import normalize, parse_inst
from app.traders import okx_profile_url, parse_trader_key, venue_of

# Real position-history records (trimmed to the fields we read)
LINEAR = {"cTime": "1793857608577", "uTime": "1793866195951", "closeAmount": "100", "openMaxAmount": "100",
          "closeAvgPx": "80563.1", "openAvgPx": "80266.5", "closeType": "allClose", "ctVal": "0.01",
          "fee": "-72.37332", "fundingFee": "0", "instId": "BTC-USDT-SWAP", "instType": "SWAP", "lever": "10",
          "mgnMode": "cross", "pnl": "296.6", "pnlRatio": "0.03", "posId": "1", "posSide": "long",
          "realizedPnl": "224.22668"}
INVERSE = {"cTime": "1793857608577", "uTime": "1793866195951", "closeAmount": "383.2", "openMaxAmount": "383.2",
           "closeAvgPx": "77457.0626208302", "openAvgPx": "76697.733508779", "closeType": "allClose", "ctVal": "100",
           "fee": "-0.0002750284402104", "fundingFee": "0", "instId": "BTC-USD-SWAP", "instType": "SWAP",
           "pnl": "0.004897923497664", "posId": "2", "posSide": "long", "realizedPnl": "0.004622895057453"}


def test_keys():
    assert parse_trader_key("0x" + "A" * 40) == "0x" + "a" * 40
    assert parse_trader_key(" okx:872835187976306688 ") == "okx:872835187976306688"
    assert parse_trader_key("okx:abc") is None and parse_trader_key("0x12") is None
    assert venue_of("okx:1234567") == "okx" and venue_of("0x" + "a" * 40) == "hyperliquid"
    assert okx_profile_url("442E2A6CB8ABD1A4").endswith("/copy-trading/account/442E2A6CB8ABD1A4")
    assert parse_inst("kPEPE-USDT-SWAP") == ("kPEPE", "linear") and parse_inst("SOL-USD-SWAP") == ("SOL", "inverse")
    with pytest.raises(ValueError):
        parse_inst("BTC-USDT-261225")


def test_normalize_linear_and_inverse():
    p = normalize(LINEAR)
    assert p["max_size"] == D(1) and p["max_notional"] == D("80266.5") and p["direction"] == "long"
    assert p["pnl"] == D("296.6") and p["fee"] == D("72.37332") and p["realized_pnl"] == D("224.22668")
    q = normalize(INVERSE)
    assert q["ct_type"] == "inverse" and q["max_notional"] == D("38320")  # 383.2 contracts x $100
    assert q["max_size"] == pytest.approx(D(38320) / D("76697.733508779"))
    assert q["pnl"] == pytest.approx(D("0.004897923497664") * D("77457.0626208302"))  # coin PnL at the close
    assert float(q["pnl"]) == pytest.approx(38320 * (1 / 76697.733508779 - 1 / 77457.0626208302) * 77457.06, rel=1e-6)
    liq = normalize({**LINEAR, "closeType": "liquidateClose", "posSide": "net", "pnl": "-500", "closeAvgPx": "75000"})
    assert liq["liquidated"] and liq["direction"] == "long"  # net mode: lost money while price fell -> long


def _old_sqlite(path):
    """A minimal copy of the old collector's schema with real-shaped rows."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE traders (author_id text, unique_name text, nick_name text, portrait text, history_status text,
          oldest_u_time int, newest_u_time int, target_days int, last_cursor_after text, failure_count int,
          last_history_error text, pool_status text, pool_priority int, first_discovered_at int, first_pool_source text,
          last_seen_official_at int, last_history_refresh int, last_profile_refresh int, updated_at int, profile_json text);
        CREATE TABLE positions (unique_name text, pos_id text, author_id text, inst_id text, raw_json text);
        CREATE TABLE leaderboard_entries (window text, sort_by text, author_id text, rank_order int, first_seen_at int,
          last_seen_at int, seen_days_count int, consecutive_seen_days int, absent_count int);
        CREATE TABLE daily_leaderboard_runs (id text, status text, business_date text, started_at int, completed_at int,
          expected_pages int, expected_total int, unique_count int, source_version text);
        CREATE TABLE daily_leaderboard_members (run_id text, author_id text, official_rank int, asset real, payload_json text);
        CREATE TABLE daily_leaderboard_observations (author_id text, first_seen_at int, last_seen_at int, seen_days int,
          streak int, currently_seen int);
        CREATE TABLE favorite_groups (id text, name text, sort_order int);
        CREATE TABLE favorites (author_id text, group_id text, note text, created_at int);
        CREATE TABLE candles (inst_id text, bar text, ts int, open real, high real, low real, close real, vol real, confirm int);
    """)
    c.executemany("INSERT INTO traders VALUES (?, ?, ?, '', ?, 0, 0, 365, '', 0, '', ?, 3, 1779540297, 'official:7d', "
                  "1791545933, 1791545933, 1791545920, 1791545933, '{\"bio\": \"x\"}')",
                  [("872835187976306688", "AAAA", "甲", "source_complete", "dropped"),
                   ("872835187976306689", None, "乙", "partial", "active")])
    part = {**LINEAR, "posId": "3", "closeType": "partClose", "closeAmount": "40"}
    c.executemany("INSERT INTO positions VALUES ('AAAA', ?, '872835187976306688', ?, ?)",
                  [(r["posId"], r["instId"], json.dumps(r)) for r in (LINEAR, INVERSE, part)] +
                  [("9", "BTC-USDT-261225", json.dumps({**LINEAR, "posId": "9", "instType": "FUTURES",
                                                         "instId": "BTC-USDT-261225"}))])
    c.execute("INSERT INTO leaderboard_entries VALUES ('30d', 'pnl_ratio', '872835187976306688', 4, 1779197431, "
              "1780082237, 3, 0, 2)")
    c.execute("INSERT INTO daily_leaderboard_runs VALUES ('r1', 'complete', '2026-11-11', 1791438544, 1791438670, 1, 2, 2, 'v')")
    c.execute("INSERT INTO daily_leaderboard_runs VALUES ('r2', 'failed', '2026-11-11', 1791438544, 0, 1, 2, 0, 'v')")
    c.execute("INSERT INTO daily_leaderboard_members VALUES ('r1', '872835187976306688', 1, 30602.98, ?)",
              (json.dumps({"pnl": 23685.82, "pnlRatio": 2.7604, "maxDrawdownPct": 218.44, "officialWinRate": 68.39,
                           "rates": [{"statTime": "261001", "value": "0.3367"}]}),))
    c.execute("INSERT INTO daily_leaderboard_observations VALUES ('872835187976306688', 1791438670, 1794332864, 6, 5, 1)")
    c.execute("INSERT INTO favorite_groups VALUES ('g1', '重点观察', 1)")
    c.execute("INSERT INTO favorites VALUES ('872835187976306688', 'g1', '拿得住', 1779118134)")
    c.executemany("INSERT INTO candles VALUES (?, ?, ?, 1, 2, 0.5, 1.5, 10, 1)",
                  [("BTC-USDT-SWAP", "1H", 1779118134000), ("BTC-USD-SWAP", "1H", 1779118134000),
                   ("ETH-USDT-SWAP", "4H", 1779118134000)])
    c.commit()
    c.close()


def test_import_bundle_is_idempotent_and_preserves_pnl(sf, tmp_path):
    _old_sqlite(tmp_path / "old.sqlite3")
    counts = export_sqlite(tmp_path / "old.sqlite3", tmp_path / "bundle")
    assert counts["positions"] == 4 and counts["daily_runs"] == 1  # failed runs are not exported
    first = import_bundle(sf, tmp_path / "bundle")
    second = import_bundle(sf, tmp_path / "bundle")
    assert first["positions"]["rows"] == 3 and first["positions"]["skipped_invalid"] == 1  # the dated future
    assert first["derived"] == second["derived"] == {"wallets": 1, "cycles": 3, "decisions": 5}
    with sf() as s:
        q = lambda sql: s.execute(text(sql)).all()  # noqa: E731
        assert q("SELECT address, handle, venue, present_in_latest FROM wallet ORDER BY address") == [
            ("okx:872835187976306688", "AAAA", "okx", False), ("okx:872835187976306689", None, "okx", False)]
        assert q("SELECT history_status, pool_status, priority, n_positions FROM okx_trader ORDER BY wallet_id")[0] == \
            ("source_complete", "watching", 0, 3)  # favourited dropped trader is watched again
        assert q("SELECT count(*) FROM okx_leaderboard_run")[0][0] == 1
        assert q("SELECT rank, win_rate, max_drawdown FROM okx_leaderboard_row") == [(1, D("0.6839"), D("2.1844"))]
        assert q("SELECT time_window, seen_days, streak, currently_seen, best_rank FROM okx_presence ORDER BY 1") == [
            ("30d", 3, 0, False, 4), ("7d", 6, 5, True, 1)]
        assert q("SELECT g.name, i.note, i.tags FROM watch_item i JOIN watch_group g ON g.id = i.group_id") == [
            ("OKX·重点观察", "拿得住", ["okx"])]
        assert q("SELECT coin, interval FROM market_candle WHERE source = 'okx' ORDER BY 1") == [("BTC", "1h"), ("ETH", "4h")]
        # cycle PnL after fees is exactly what OKX reports as realized
        okx_total = q("SELECT round(sum(realized_pnl), 8) FROM okx_position")[0][0]
        assert q("SELECT round(sum(realized_pnl - fees), 8) FROM position_cycle WHERE version = 'okx1'")[0][0] == okx_total
        assert q("SELECT status, count(*) FROM position_cycle GROUP BY 1 ORDER BY 1") == [("closed", 2), ("open", 1)]
        assert q("SELECT exits FROM position_cycle WHERE status = 'open'") == [([],)]  # empty array, not JSON null
        assert q("SELECT kind, side, count(*) FROM trade_decision GROUP BY 1, 2 ORDER BY 1") == [
            ("close", "sell", 2), ("open", "buy", 3)]


# --- O2: leaderboard crawl and history sync against a mocked OKX API ---

import asyncio  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.okx.client import OkxClient  # noqa: E402
from app.okx.leaderboard import run_window  # noqa: E402
from app.okx.sync import sync_history  # noqa: E402
from app.sources.hl_client import WeightBucket  # noqa: E402

NOW = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)  # 09:00 in Asia/Shanghai


def _client(handler) -> OkxClient:
    return OkxClient("https://okx.test", 600, transport=httpx.MockTransport(handler), bucket=WeightBucket(10_000),
                     max_retries=0)


def _board(ids, version="v1", size=2, page=1):
    total = len(ids)
    rows = ids[(page - 1) * size: page * size]
    return {"code": "0", "data": {"dataVersion": version, "pages": -(-total // size), "total": total, "ranks": [
        {"authorId": i, "nickName": f"n{i}", "asset": "1000", "pnl": "10", "pnlRatio": "0.1", "winRate": "0.6",
         "maxRetreat": "0.2", "rates": [{"statTime": "261116", "value": "0.1"}]} for i in rows]}}


def _board_handler(ids, versions=None):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params["page"])
        calls.append(page)
        version = (versions or {}).get(len(calls), "v1")
        return httpx.Response(200, json=_board(ids, version, int(req.url.params["size"]), page))
    return handler, calls


def _crawl(sf, ids, now, versions=None):
    handler, calls = _board_handler(ids, versions)

    async def go():
        async with _client(handler) as c:
            return await run_window(sf, c, "7d", 2, "Asia/Shanghai", now=now)
    return asyncio.run(go()), calls


def test_daily_crawl_presence_streaks_and_restart(sf):
    r, calls = _crawl(sf, ["1000001", "1000002", "1000003"], NOW)
    assert r == {"window": "7d", "full": True, "total": 3, "members": 3, "new": 3, "absent": 0}
    assert calls == [1, 2, 1]  # two pages + the final check of page one
    # next day: one trader left, one new; a republish during the crawl restarts it
    r, calls = _crawl(sf, ["1000002", "1000003", "1000004"], NOW + timedelta(days=1), versions={2: "v2"})
    assert r["new"] == 1 and r["absent"] == 1 and calls == [1, 2, 1, 2, 1]
    with sf() as s:
        rows = {r[0]: tuple(r[1:]) for r in s.execute(text("""
            SELECT w.venue_uid, p.seen_days, p.streak, p.currently_seen FROM okx_presence p
            JOIN wallet w ON w.id = p.wallet_id""")).all()}
        assert rows == {"1000001": (1, 0, False), "1000002": (2, 2, True), "1000003": (2, 2, True),
                        "1000004": (1, 1, True)}
        assert s.execute(text("SELECT history_status, pool_status, priority, asset FROM okx_trader LIMIT 1")).one() == \
            ("partial", "active", 1, D(1000))
        assert s.execute(text("SELECT status, count(*) FROM okx_leaderboard_run GROUP BY 1")).all() == [("complete", 2)]
        assert s.execute(text("SELECT win_rate, max_drawdown FROM okx_leaderboard_row LIMIT 1")).one() == \
            (D("0.6"), D("0.2"))


def test_crawl_rejects_inconsistent_pages(sf):
    def handler(req):
        page = int(req.url.params["page"])
        body = _board(["1000001", "1000002", "1000003"], size=2, page=page)
        if page == 2:
            body["data"]["ranks"] = body["data"]["ranks"] * 2  # 2 rows where 1 is expected
        return httpx.Response(200, json=body)

    async def go():
        async with _client(handler) as c:
            return await run_window(sf, c, "7d", 2, "Asia/Shanghai", now=NOW)
    with pytest.raises(Exception, match="rows, expected"):
        asyncio.run(go())
    with sf() as s:
        assert s.execute(text("SELECT status FROM okx_leaderboard_run")).scalar_one() == "failed"
        assert s.execute(text("SELECT count(*) FROM wallet")).scalar_one() == 0


def _pos(pid: int, t_ms: int) -> dict:
    return {**LINEAR, "posId": str(pid), "cTime": str(t_ms - 3_600_000), "uTime": str(t_ms)}


def test_history_backfill_increment_and_backoff(sf):
    _crawl(sf, ["1000001"], NOW)
    with sf() as s:
        s.execute(text("UPDATE wallet SET handle = 'H1'"))
        s.commit()
    end = int(NOW.timestamp() * 1000)
    day = 86_400_000
    book = [_pos(i, end - i * day) for i in range(1, 251)]  # one closed position a day, newest first
    seen_after = []

    def handler(req):
        after = req.url.params.get("after")
        seen_after.append(after)
        rows = [p for p in book if after is None or int(p["uTime"]) < int(after)]
        return httpx.Response(200, json={"code": "0", "data": rows[:int(req.url.params["limit"])]})

    settings = Settings(okx_history_max_pages=30)

    async def go(h, now=NOW):
        async with _client(h) as c:
            return await sync_history(sf, settings, c, now=now)
    r = asyncio.run(go(handler))
    assert r == {"traders": 1, "stored": 250, "errors": 0, "completed": 1}
    with sf() as s:
        t = s.execute(text("SELECT history_status, n_positions FROM okx_trader")).one()
        assert tuple(t) == ("source_complete", 250)  # fewer than 365 days exist: the source ran out
        assert s.execute(text("SELECT count(*) FROM position_cycle WHERE version = 'okx1'")).scalar_one() == 250
    # an hour later two new positions closed; the increment reads only the top page
    book[:0] = [_pos(1001, end + 2 * 3_600_000), _pos(1000, end + 3_600_000)]
    seen_after.clear()
    r = asyncio.run(go(handler, NOW + timedelta(hours=1, minutes=1)))
    assert r["stored"] == 100 and seen_after[0] is None
    with sf() as s:
        assert s.execute(text("SELECT n_positions FROM okx_trader")).scalar_one() == 252
    # errors back off without losing the state
    r = asyncio.run(go(lambda req: httpx.Response(500), NOW + timedelta(hours=3)))
    assert r["errors"] == 1
    with sf() as s:
        st = s.execute(text("SELECT history_status, failure_count, retry_after > :t FROM okx_trader"),
                       {"t": NOW + timedelta(hours=3)}).one()
        assert tuple(st) == ("source_complete", 1, True)


# --- O4: live positions and venue-isolated simulations ---

from app.okx.live import mids_from_tickers, parse_current  # noqa: E402
from app.sim.engine import step_simulations  # noqa: E402

SPECS = {"BTC-USDT-SWAP": ("linear", D("0.01")), "BTC-USD-SWAP": ("inverse", D("100")),
         "ETH-USDT-SWAP": ("linear", D("0.1"))}


def test_parse_current_nets_hedges_and_converts_contracts():
    raw = [
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "300", "avgPx": "80000", "markPx": "81000",
         "lever": "10", "liqPx": "70000", "upl": "300", "mgnMode": "cross"},
        {"instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "100", "avgPx": "82000", "markPx": "81000",
         "lever": "5", "upl": "100", "mgnMode": "isolated"},
        {"instId": "BTC-USD-SWAP", "posSide": "net", "pos": "-810", "avgPx": "81000", "markPx": "81000", "upl": "0"},
        {"instId": "ETH-USDT-SWAP", "posSide": "long", "pos": "10", "avgPx": "3000", "markPx": "3000"},
        {"instId": "ETH-USDT-SWAP", "posSide": "short", "pos": "10", "avgPx": "3010", "markPx": "3000"},
    ]
    pos, ntl = parse_current(raw, SPECS)
    # BTC: +3 - 1 (linear) - 1 (810 x $100 / 81000, inverse) = +1 BTC; ETH fully hedged -> not a position
    assert set(pos) == {"BTC"} and pos["BTC"].szi == D(1)
    assert pos["BTC"].entry_px == D(80000) and pos["BTC"].leverage == 10 and pos["BTC"].leverage_type == "cross"
    assert pos["BTC"].position_value == D(81000) and ntl == D(81000)
    mids = mids_from_tickers([{"instId": "BTC-USDT-SWAP", "bidPx": "100", "askPx": "102", "last": "99"},
                              {"instId": "BTC-USD-SWAP", "bidPx": "1", "askPx": "1"},
                              {"instId": "ETH-USDT-SWAP", "bidPx": "", "askPx": "", "last": "3000"}])
    assert mids == {"BTC": D(101), "ETH": D(3000)}


def test_simulations_step_only_with_their_leaders_venue(sf):
    with sf() as s:
        ids = {}
        for addr, venue in (("0x" + "a" * 40, "hyperliquid"), ("okx:1000001", "okx")):
            ids[venue] = s.execute(text("""
                INSERT INTO wallet (address, venue, first_seen_at, first_seen_source, last_seen_at, present_in_latest)
                VALUES (:a, :v, now(), 't', now(), false) RETURNING id"""), {"a": addr, "v": venue}).scalar_one()
            s.execute(text("INSERT INTO live_account (wallet_id, account_value, fetched_at) VALUES (:w, 10000, now())"),
                      {"w": ids[venue]})
            s.execute(text("INSERT INTO live_position (wallet_id, coin, szi, liq_risk, updated_at) "
                           "VALUES (:w, 'BTC', 1, false, now())"), {"w": ids[venue]})
            s.execute(text("""
                INSERT INTO copy_sim (owner, name, leader_wallet_id, status, capital, sizing, size_factor, max_leverage,
                  follow_adjust, copy_existing, delay_seconds, fee_bps, slippage_bps, min_trade_usd, rebalance_pct,
                  cash, peak_equity, state)
                VALUES ('default', :n, :w, 'running', 1000, 'equity_ratio', 1, 10, true, true, 0, 5, 0, 10, 5, 1000,
                        1000, '{}')"""), {"n": venue, "w": ids[venue]})
        s.commit()
    assert step_simulations(sf, {"BTC": D(80000)}, venue="okx")["sims"] == 1
    with sf() as s:
        held = dict(s.execute(text("""SELECT c.name, count(p.coin) FROM copy_sim c
                                      LEFT JOIN sim_position p ON p.sim_id = c.id GROUP BY c.name""")).all())
        assert held == {"okx": 1, "hyperliquid": 0}
