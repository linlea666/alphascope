"""Integration tests against a real Postgres test database (real-row fixtures, no network)."""

import copy
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.config import Settings
from app.ingest.leaderboard_ingest import LeaderboardInput, ingest_leaderboard
from app.ingest.portfolio_ingest import parse_portfolio, store_clearinghouse, store_portfolio, _aggregate_flag
from app.models import Wallet
from app.quant.quality import AGGREGATE_ACCOUNT
from app.sources.raw_store import LocalRawStore
from tests.conftest import load_fixture

T0 = datetime(2026, 11, 15, 12, tzinfo=UTC)


def run(sf, tmp_path, doc, now):
    settings = Settings(leaderboard_snapshot_hours=6)
    return ingest_leaderboard(sf, settings, LocalRawStore(tmp_path), LeaderboardInput(json.dumps(doc).encode()), now)


def scalar(sf, sql, **kw):
    with sf() as s:
        return s.execute(text(sql), kw).scalar_one()


def test_initial_import_then_idempotent(sf, tmp_path):
    doc = load_fixture("leaderboard_sample.json")
    n = len(doc["leaderboardRows"])
    r1 = run(sf, tmp_path, doc, T0)
    assert r1["status"] == "success" and r1["initial_import"] and r1["new_wallets"] == n
    assert r1["rows_persisted"] is True
    assert scalar(sf, "SELECT count(*) FROM leaderboard_row") == n
    assert scalar(sf, "SELECT count(*) FROM wallet_tier_history") == n

    r2 = run(sf, tmp_path, doc, T0 + timedelta(hours=1))
    assert r2["status"] == "not_modified" and r2["reason"] == "same_content"
    assert scalar(sf, "SELECT count(*) FROM wallet") == n
    assert scalar(sf, "SELECT count(*) FROM leaderboard_snapshot") == 1


def test_new_disappeared_and_snapshot_cadence(sf, tmp_path):
    doc = load_fixture("leaderboard_sample.json")
    run(sf, tmp_path, doc, T0)
    gone = doc["leaderboardRows"][0]["ethAddress"]

    smaller = copy.deepcopy(doc)
    smaller["leaderboardRows"] = smaller["leaderboardRows"][1:]
    newcomer = copy.deepcopy(doc["leaderboardRows"][1])
    newcomer["ethAddress"] = "0x" + "b" * 40
    smaller["leaderboardRows"].append(newcomer)
    r = run(sf, tmp_path, smaller, T0 + timedelta(hours=1))
    assert r["new_wallets"] == 1 and r["disappeared_wallets"] == 1
    assert r["rows_persisted"] is False  # < 6h since last persisted snapshot
    with sf() as s:
        w = s.query(Wallet).filter_by(address=gone).one()
        assert w.present_in_latest is False and w.removed_at is not None
        assert w.first_seen_at == T0  # history is not overwritten

    r = run(sf, tmp_path, doc, T0 + timedelta(hours=7))
    assert r["rows_persisted"] is True and r["new_wallets"] == 0
    with sf() as s:
        w = s.query(Wallet).filter_by(address=gone).one()
        assert w.present_in_latest is True and w.removed_at is None
    assert scalar(sf, "SELECT count(*) FROM raw_object WHERE source = 'leaderboard'") == 3


def test_failed_run_is_recorded(sf, tmp_path):
    settings = Settings()
    r = ingest_leaderboard(sf, settings, LocalRawStore(tmp_path), LeaderboardInput(b'{"oops": 1}'), T0)
    assert r["status"] == "failed"
    assert scalar(sf, "SELECT count(*) FROM wallet") == 0
    assert scalar(sf, "SELECT error IS NOT NULL FROM ingest_run WHERE id = :i", i=r["run_id"])


def test_store_portfolio_and_clearinghouse(sf, tmp_path):
    run(sf, tmp_path, load_fixture("leaderboard_sample.json"), T0)
    parsed = parse_portfolio(load_fixture("portfolio_sample.json"))
    now = parsed["allTime"][-1].ts
    with sf() as s:
        wid = s.execute(text("SELECT id FROM wallet ORDER BY id LIMIT 1")).scalar_one()
        av = store_portfolio(s, wid, parsed, now, None, now)
        own = store_clearinghouse(s, wid, load_fixture("clearinghouse_sample.json"), now, None)
        s.commit()
        assert av == parsed["allTime"][-1].account_value and own > 0
        # re-storing the same observation is a no-op for points, an upsert for metrics
        store_portfolio(s, wid, parsed, now, None, now)
        s.commit()
    assert scalar(sf, "SELECT count(*) FROM wallet_metric") == 5
    assert scalar(sf, "SELECT count(*) FROM equity_point") == sum(len(parsed[k]) for k in ("day", "week", "month", "allTime"))


def test_aggregate_flag():
    from decimal import Decimal
    assert AGGREGATE_ACCOUNT in _aggregate_flag([], Decimal(57_000_000), Decimal(38_000))
    assert _aggregate_flag([AGGREGATE_ACCOUNT], Decimal(40_000), Decimal(38_000)) == []
    assert _aggregate_flag([], Decimal(500), Decimal(10)) == []  # tiny accounts are not judged


def test_abandoned_runs_are_closed(sf):
    from app.ingest.runs import mark_abandoned_runs
    with sf() as s:
        s.execute(text("INSERT INTO ingest_run (kind, status, started_at, stats) VALUES "
                       "('enrich', 'running', :old, '{}'), ('enrich', 'running', :fresh, '{}')"),
                  {"old": T0 - timedelta(hours=3), "fresh": T0})
        s.commit()
    assert mark_abandoned_runs(sf, now=T0 + timedelta(minutes=5)) == 1
    assert scalar(sf, "SELECT count(*) FROM ingest_run WHERE status = 'running'") == 1
