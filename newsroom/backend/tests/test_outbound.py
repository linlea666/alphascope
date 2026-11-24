"""The outbound API: named tokens list ready posts, claim one, report back, fetch the card and send metrics."""

from sqlalchemy import text

from nr.publish import service as pub
from tests.test_api import client, login  # noqa: F401  (fixture)
from tests.test_publish_center import drafted


def ready(sf):
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = now() + interval '1 hour' WHERE id = :a"), {"a": art})
        s.execute(text("UPDATE user_role SET role = 'admin' WHERE user_id = :u"), {"u": nr["chief"]})
        s.commit()
    return nr, art


def test_a_token_takes_a_post_reports_it_and_sends_metrics(client, sf):
    nr, art = ready(sf)
    h = login(client, "chief")
    raw = client.post("/api/api-tokens", json={"name": "my extension"}, headers=h).json()["token"]
    other = client.post("/api/api-tokens", json={"name": "rpa-1", "channel": "rpa"}, headers=h).json()["token"]
    bot, bot2 = {"Authorization": f"Bearer {raw}"}, {"Authorization": f"Bearer {other}"}
    assert client.get("/api/feed/ready", headers={"Authorization": "Bearer nope"}).status_code == 401
    posts = client.get("/api/feed/ready", params={"language": "zh"}, headers=bot).json()["posts"]
    assert [p["article_id"] for p in posts] == [art] and posts[0]["posts"][0].startswith("一个已验证")
    assert client.post(f"/api/feed/{art}/claim", headers=bot).status_code == 200
    assert client.post(f"/api/feed/{art}/claim", headers=bot2).status_code == 409  # taken
    assert client.get("/api/feed/ready", headers=bot2).json()["posts"] == []  # and hidden from the other client
    r = client.post(f"/api/feed/{art}/result", json={"ok": True}, headers=bot)
    assert r.status_code == 200, r.text
    pid = r.json()["publication_id"]
    with sf() as s:
        rec = s.execute(text("SELECT channel, api_token_id IS NOT NULL FROM publication_record WHERE id = :p"), {"p": pid}).one()
    assert rec == ("extension", True)
    assert client.post(f"/api/feed/publications/{pid}/metrics", json={"impressions": 1200, "likes": 31},
                       headers=bot).status_code == 200
    with sf() as s:
        assert s.execute(text("SELECT impressions, source FROM performance_snapshot")).one() == (1200, "api")


def test_a_failed_post_goes_back_and_disabled_or_scoped_tokens_are_refused(client, sf):
    nr, art = ready(sf)
    h = login(client, "chief")
    raw = client.post("/api/api-tokens", json={"name": "rpa", "channel": "rpa"}, headers=h).json()
    bot = {"Authorization": f"Bearer {raw['token']}"}
    client.post(f"/api/feed/{art}/claim", headers=bot)
    assert client.post(f"/api/feed/{art}/result", json={"ok": False, "error": "login wall"}, headers=bot).json()["requeued"]
    with sf() as s:
        assert s.execute(text("SELECT claimed_by_token FROM article WHERE id = :a"), {"a": art}).scalar() is None
    scoped = client.post("/api/api-tokens", json={"name": "en only", "account_ids": [nr["en"]]}, headers=h).json()["token"]
    assert client.post(f"/api/feed/{art}/claim", headers={"Authorization": f"Bearer {scoped}"}).status_code == 403
    client.post(f"/api/api-tokens/{raw['id']}/disable", headers=h)
    assert client.get("/api/feed/ready", headers=bot).status_code == 401
    with sf() as s:
        s.execute(text("UPDATE article SET status = 'expired' WHERE id = :a"), {"a": art})
        s.commit()
    fresh = client.post("/api/api-tokens", json={"name": "x"}, headers=h).json()["token"]
    assert client.get("/api/feed/ready", headers={"Authorization": f"Bearer {fresh}"}).json()["posts"] == []
