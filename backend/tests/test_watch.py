import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_session
from app.main import app

A = "0x" + "a" * 40


@pytest.fixture
def client(sf):
    with sf() as s:
        s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at, present_in_latest,
                          account_value) VALUES (:a, now(), 't', now(), true, 1000)"""), {"a": A})
        s.commit()

    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_groups_membership_alias_and_priority(client, sf):
    m = client.get(f"/api/v1/watch/wallet/{A}").json()
    assert len(m["groups"]) == 1 and not m["groups"][0]["member"]  # default group created on demand
    default = m["groups"][0]["id"]
    g2 = client.post("/api/v1/watch/groups", json={"name": "BTC 大周期", "color": "#f90"}).json()["id"]
    assert client.post("/api/v1/watch/groups", json={"name": "BTC 大周期"}).status_code == 409

    r = client.put(f"/api/v1/watch/wallet/{A.upper().replace('0X', '0x')}",
                   json={"group_ids": [default, g2], "alias": "抄底哥", "note": "65k 抄底", "tags": ["btc"]}).json()
    assert {g["id"] for g in r["groups"] if g["member"]} == {default, g2}
    assert r["alias"] == "抄底哥" and r["tags"] == ["btc"]
    assert client.get("/api/v1/watch/aliases").json()["aliases"] == {A: "抄底哥"}
    with sf() as s:
        reason, prio = s.execute(text("SELECT reason, priority FROM fill_sync")).one()
        assert reason == "watchlist" and prio >= 1_000_000

    w = client.get("/api/v1/watch").json()
    assert len(w["items"]) == 2 and w["items"][0]["alias"] == "抄底哥"

    # replace semantics: only g2 left; then unwatch entirely; clearing the alias removes it
    client.put(f"/api/v1/watch/wallet/{A}", json={"group_ids": [g2]})
    assert len(client.get("/api/v1/watch").json()["items"]) == 1
    client.put(f"/api/v1/watch/wallet/{A}", json={"group_ids": [], "alias": ""})
    assert client.get("/api/v1/watch").json()["items"] == []
    assert client.get("/api/v1/watch/aliases").json()["aliases"] == {}

    assert client.patch(f"/api/v1/watch/groups/{g2}", json={"name": "大周期"}).status_code == 200
    assert client.delete(f"/api/v1/watch/groups/{g2}").status_code == 200
    assert client.delete(f"/api/v1/watch/groups/{default}").status_code == 409  # keep at least one group


def test_validation(client):
    assert client.put(f"/api/v1/watch/wallet/{A}", json={"group_ids": [99999]}).status_code == 422
    assert client.get("/api/v1/watch/wallet/0x123").status_code == 422
    assert client.get("/api/v1/watch/wallet/0x" + "0" * 40).status_code == 404
    assert client.post("/api/v1/watch/groups", json={"name": ""}).status_code == 422
