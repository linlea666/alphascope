"""The publishing path for one person: one click to ready, "I posted it" without a link, expired drafts confirmed
explicitly, trials promoted to real articles."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from nr.articles.versions import Draft, save_version
from nr.desk import assignment
from nr.publish import service as pub
from tests.helpers import NOW, candidate_event, newsroom

ZH = "一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。"


def drafted(sf, source="editor"):
    nr = newsroom(sf)
    eid = candidate_event(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["zh"], source=source, status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        save_version(s, art, Draft(body=ZH, facts_used=["position.notional_usd", "position.leverage"]), origin="ai")
        s.commit()
    return nr, eid, art


def test_quick_approve_and_record_without_a_link(sf):
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        assert s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar() == "approved"
        pid = pub.mark_published(s, art, nr["chief"], None, NOW, NOW)
        s.commit()
        rec = s.execute(text("SELECT x_url, channel, status FROM publication_record WHERE id = :p"), {"p": pid}).one()
    assert rec == (None, "manual", "published")


def test_an_expired_draft_needs_an_explicit_confirmation(sf):
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET status = 'expired' WHERE id = :a"), {"a": art})
        with pytest.raises(HTTPException) as e:
            pub.mark_published(s, art, nr["chief"], None, NOW, NOW)
        assert e.value.status_code == 409 and "expired" in e.value.detail
        pub.mark_published(s, art, nr["chief"], None, NOW, NOW, allow_expired=True, channel="extension")
        s.commit()
        assert s.execute(text("SELECT channel FROM publication_record")).scalar() == "extension"


def test_promote_trial_keeps_the_draft(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf, source="trial")

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        assert c.post(f"/api/articles/{art}/promote", headers=h).status_code == 200
        assert c.post(f"/api/articles/{art}/promote", headers=h).status_code == 409  # not a trial any more
        r = c.post(f"/api/articles/{art}/review", json={"action": "quick_approve"}, headers=h)
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
    with sf() as s:
        body, status = s.execute(text("""SELECT v.body, a.status FROM article a JOIN article_version v
                                         ON v.id = a.current_version_id WHERE a.id = :a"""), {"a": art}).one()
    assert body == ZH and status == "approved"
