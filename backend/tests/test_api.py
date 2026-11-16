import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.ingest.leaderboard_ingest import LeaderboardInput, ingest_leaderboard
from app.main import app
from app.sources.raw_store import LocalRawStore
from tests.conftest import load_fixture


@pytest.fixture
def client(sf, tmp_path):
    doc = load_fixture("leaderboard_sample.json")
    ingest_leaderboard(sf, Settings(), LocalRawStore(tmp_path), LeaderboardInput(json.dumps(doc).encode()),
                       datetime(2026, 11, 15, tzinfo=UTC))

    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_rankings_default_filter_excludes_noise(client):
    raw = client.get("/api/v1/rankings", params={"raw": True, "window": "alltime"}).json()
    clean = client.get("/api/v1/rankings", params={"window": "alltime"}).json()
    assert raw["total"] == len(load_fixture("leaderboard_sample.json")["leaderboardRows"])
    assert clean["total"] < raw["total"]
    for row in clean["rows"]:
        assert not set(row["flags"]) & set(clean["excluded_flags"])
        assert row["vlm"] > 0


def test_rankings_sorting_and_paging(client):
    d = client.get("/api/v1/rankings", params={"raw": True, "sort": "account_value", "page_size": 5}).json()
    values = [r["account_value"] for r in d["rows"]]
    assert values == sorted(values, reverse=True)
    assert [r["rank"] for r in d["rows"]] == [1, 2, 3, 4, 5]
    p2 = client.get("/api/v1/rankings", params={"raw": True, "sort": "account_value", "page_size": 5, "page": 2}).json()
    assert p2["rows"][0]["rank"] == 6 and p2["rows"][0]["account_value"] <= values[-1]


def test_rankings_validation(client):
    assert client.get("/api/v1/rankings", params={"sort": "nope"}).status_code == 422
    assert client.get("/api/v1/rankings", params={"window": "90d"}).status_code == 422
    assert client.get("/api/v1/rankings", params={"exclude_flags": "made_up"}).status_code == 422


def test_wallet_detail(client):
    addr = load_fixture("leaderboard_sample.json")["leaderboardRows"][0]["ethAddress"]
    d = client.get(f"/api/v1/wallets/{addr.upper().replace('0X', '0x')}").json()
    assert d["wallet"]["address"] == addr
    assert set(d["leaderboard"]) == {"day", "week", "month", "alltime"}
    assert len(d["snapshots"]) == 1 and d["metrics"] == []
    assert client.get("/api/v1/wallets/0x123").status_code == 422
    assert client.get("/api/v1/wallets/0x" + "0" * 40).status_code == 404


def test_overview_and_health(client):
    o = client.get("/api/v1/overview").json()
    assert o["counts"]["total"] == o["counts"]["present"] > 0
    assert o["counts"]["new_24h"] == 0  # the seed import is not "newly discovered"
    h = client.get("/api/v1/data-health").json()
    assert h["tables"]["wallet"] == o["counts"]["total"]
    assert h["latest_success"][0]["kind"] == "leaderboard"
