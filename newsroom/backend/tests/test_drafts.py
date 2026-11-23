from datetime import UTC, datetime

from sqlalchemy import text

from nr.jobs.handlers import Deps, run_pending
from tests.fakes import WHALE, FakeFeed, FakeProvider, context, ev, health, position
from tests.helpers import NOW, draft_output, newsroom
from tests.pipeline import pipeline
from tests.test_api import client, login  # noqa: F401  (fixture)

ZH = "一个地址开出约3000万美元 BTC 多单，杠杆20倍。"


def blocked_event(sf):
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)])
    feed.health_doc = health(hl="stale")
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid, state = s.execute(text("SELECT id, state FROM news_event")).one()
    assert state == "blocked"
    return eid


def jobs(sf, provider):
    return run_pending(sf, Deps(feed=FakeFeed().client, provider=provider, now=lambda: datetime.now(UTC)),
                       ("draft_generate",))


def test_trial_on_blocked_event_and_paused_account(client, sf):
    nr = newsroom(sf, accounts_active=False)
    eid = blocked_event(sf)
    h = login(client, "chief")
    with sf() as s:
        models = s.execute(text("SELECT id FROM ai_model_config ORDER BY id")).scalars().all()
    r = client.post(f"/api/events/{eid}/trial", json={"account_ids": [nr["zh"]], "model_ids": models}, headers=h).json()
    art = r["articles"][0]["article_id"]
    assert len(r["articles"][0]["jobs"]) == 2
    done = jobs(sf, FakeProvider(draft_output("zh", ZH), draft_output("zh", ZH.replace("约", ""))))
    assert len(done) == 2 and all(d["result"]["article_id"] == art for d in done)
    with sf() as s:
        assert s.execute(text("SELECT state FROM news_event")).scalar() == "blocked"  # untouched
        assert s.execute(text("SELECT count(*) FROM article_version WHERE article_id = :a"), {"a": art}).scalar() == 2
    # a single-model trial on the same pair reuses the trial article and its draft becomes current
    again = client.post(f"/api/events/{eid}/trial", json={"account_ids": [nr["zh"]]}, headers=h)
    assert again.status_code == 200 and again.json()["articles"][0]["article_id"] == art
    assert jobs(sf, FakeProvider(draft_output("zh", ZH)))[0]["result"]["made_current"] is True
    # trials cannot be reviewed or published
    assert client.post(f"/api/articles/{art}/review", json={"action": "submit"}, headers=h).status_code == 409
    listed = client.get("/api/articles", params={"trial": True}, headers=h).json()["rows"]
    assert [x["id"] for x in listed] == [art] and listed[0]["source"] == "trial" and listed[0]["versions"] == 3
    ai = client.get("/api/drafts/ai", headers=h).json()
    assert len(ai["versions"]) == 3 and {v["model"] for v in ai["versions"]} == {"model-primary", "model-fallback"}
    md = client.get(f"/api/articles/{art}/export", headers=h)
    assert md.status_code == 200 and "## Facts cited" in md.text and "attachment" in md.headers["content-disposition"]


def test_trial_does_not_take_the_real_slot(client, sf):
    nr = newsroom(sf)
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000)]
    feed.contexts[WHALE] = context(positions=[position(szi=300)],
                                   styles=[{"style": "trend", "version": "s2", "status": "verified", "confidence": "1",
                                            "n": 40, "oos": None, "computed_at": NOW.isoformat()}])
    pipeline(sf, feed, NOW)
    with sf() as s:
        eid = s.execute(text("SELECT id FROM news_event")).scalar()
    h = login(client, "chief")
    client.post(f"/api/events/{eid}/trial", json={"account_ids": [nr["zh"]]}, headers=h)
    r = client.post(f"/api/events/{eid}/assignments", json={"account_id": nr["zh"]}, headers=h)
    assert r.status_code == 200
    with sf() as s:
        assert s.execute(text("SELECT state FROM news_event")).scalar() == "candidate"


def test_trash_restore_and_bulk(client, sf):
    nr = newsroom(sf)
    eid = blocked_event(sf)
    h = login(client, "chief")
    arts = [client.post(f"/api/events/{eid}/trial", json={"account_ids": [acc]}, headers=h).json()["articles"][0]["article_id"]
            for acc in (nr["zh"], nr["en"])]
    client.post(f"/api/articles/{arts[0]}/versions", json={"body": ZH, "facts_used": ["position.notional_usd"]}, headers=h)
    assert client.post(f"/api/articles/{arts[0]}/trash", json={"reason": "test"}, headers=h).status_code == 200
    assert [x["id"] for x in client.get("/api/articles", headers=h).json()["rows"]] == [arts[1]]
    trashed = client.get("/api/articles", params={"trash": True}, headers=h).json()["rows"]
    assert trashed[0]["id"] == arts[0] and trashed[0]["deleted_reason"] == "test"
    # trashed articles take no edits until restored
    assert client.post(f"/api/articles/{arts[0]}/versions", json={"body": ZH}, headers=h).status_code == 409
    assert client.post(f"/api/articles/{arts[0]}/restore", headers=h).status_code == 200
    detail = client.get(f"/api/articles/{arts[0]}", headers=h).json()["article"]
    assert detail["deleted_at"] is None and detail["status"] == "draft_ready" and len(
        client.get(f"/api/articles/{arts[0]}", headers=h).json()["versions"]) == 1
    with sf() as s:  # a published article cannot be trashed: bulk reports it and carries on
        s.execute(text("UPDATE article SET status = 'published' WHERE id = :a"), {"a": arts[1]})
        s.commit()
    r = client.post("/api/articles/bulk", json={"ids": arts, "action": "trash"}, headers=h).json()
    assert r["done"] == [arts[0]] and r["failed"][0]["id"] == arts[1]
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action IN ('article.trash', 'article.restore')"
                              )).scalar() == 3
