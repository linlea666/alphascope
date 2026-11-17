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
        assert q("SELECT kind, side, count(*) FROM trade_decision GROUP BY 1, 2 ORDER BY 1") == [
            ("close", "sell", 2), ("open", "buy", 3)]
