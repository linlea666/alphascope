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


def review_out(verdict, publishable=4):
    return {"verdict": verdict, "scores": {"ai_tone": 4, "native": 4, "hook": 3, "clarity": 4, "style_fit": 4, "risk": 5,
                                           "publishable": publishable},
            "issues": [{"quote": "开出约3000万美元", "problem": "开头平淡", "fix": "先说他是谁"}] if verdict != "approve" else [],
            "summary": "可以发" if verdict == "approve" else "开头太平", "revision_note": "" if verdict == "approve" else "第一句先抛反差"}


def run_jobs(sf, prov, kinds=("draft_review", "draft_generate")):
    from nr.jobs.handlers import Deps, run_pending
    from tests.fakes import FakeFeed
    return run_pending(sf, Deps(feed=FakeFeed().client, provider=prov, now=lambda: NOW), kinds)


def first_review_job(sf, art):
    from nr.jobs.queue import enqueue
    with sf() as s:
        vid = s.execute(text("SELECT current_version_id FROM article WHERE id = :a"), {"a": art}).scalar()
        enqueue(s, "draft_review", {"version_id": vid}, dedupe_key=f"review:{vid}", now=NOW)
        s.commit()


def test_the_reviewer_approves_into_ready_to_post(sf):
    from tests.fakes import FakeProvider
    nr, eid, art = drafted(sf)
    first_review_job(sf, art)
    run_jobs(sf, FakeProvider(review_out("approve")))
    with sf() as s:
        status = s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar()
        rev = s.execute(text("SELECT verdict, scores->>'publishable' FROM article_review")).one()
    assert status == "approved" and rev == ("approve", "4")


def test_revise_gets_one_editor_pass_then_a_person_decides(sf):
    from tests.fakes import FakeProvider
    from tests.helpers import draft_output
    nr, eid, art = drafted(sf)
    first_review_job(sf, art)
    prov = FakeProvider(review_out("revise"), draft_output("zh", "这头巨鲸开出约3000万美元 BTC 多单，20倍。",
                                                          ["position.notional_usd", "position.leverage"]),
                        review_out("revise"))
    run_jobs(sf, prov)
    with sf() as s:
        reviews = s.execute(text("SELECT count(*) FROM article_review")).scalar()
        versions = s.execute(text("SELECT count(*) FROM article_version WHERE article_id = :a"), {"a": art}).scalar()
        status = s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar()
    assert reviews == 2 and versions == 2 and status == "draft_ready"  # no third round: it waits for a person
    assert "第一句先抛反差" in prov.requests[1]["user"]  # the editor saw the reviewer's instructions


def test_trials_are_reviewed_but_never_auto_approved(sf):
    from tests.fakes import FakeProvider
    nr, eid, art = drafted(sf, source="trial")
    first_review_job(sf, art)
    run_jobs(sf, FakeProvider(review_out("approve")))
    with sf() as s:
        assert s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar() == "draft_ready"
