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


def test_the_publish_center_lists_ready_posts_with_their_review_and_reply(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = now() + interval '1 hour' WHERE id = :a"), {"a": art})
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        d = c.get("/api/publish", params={"tab": "ready", "language": "zh"}, headers=h).json()
        assert [x["id"] for x in d["cards"]] == [art] and d["counts"]["ready"] == 1
        assert d["cards"][0]["body"] == ZH and d["cards"][0]["expires_at"] is not None
        assert c.get("/api/publish", params={"tab": "ready", "language": "en"}, headers=h).json()["cards"] == []
        assert c.post(f"/api/articles/{art}/publish", json={}, headers=h).status_code == 200  # "I posted it"
        d = c.get("/api/publish", params={"tab": "published"}, headers=h).json()
        assert d["cards"][0]["publication"]["channel"] == "manual"
    finally:
        app.dependency_overrides.clear()


def test_an_edited_post_is_checked_again_and_the_record_says_so(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        same = c.post(f"/api/articles/{art}/check-text", json={"text": ZH + "\n"}, headers=h).json()
        assert same["matches_version"] and not same["blocked"]
        wrong = c.post(f"/api/articles/{art}/check-text", json={"text": ZH.replace("3000万", "9000万")}, headers=h).json()
        assert not wrong["matches_version"] and wrong["blocked"]  # a number the facts don't support
        edited = ZH.replace("杠杆20倍。", "20倍杠杆。")
        assert c.post(f"/api/articles/{art}/publish", json={"final_text": edited}, headers=h).status_code == 200
        pub_ = c.get("/api/publish", params={"tab": "published"}, headers=h).json()["cards"][0]["publication"]
        assert pub_["matches_version"] is False and pub_["guard_status"] in ("pass", "warn")
    finally:
        app.dependency_overrides.clear()


def test_pair_view_brings_the_other_language_from_any_tab(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["en"], source="editor", status="accepted", user_id=nr["chief"])
        en = assignment.accept(s, aid, nr["chief"])
        save_version(s, en, Draft(body="A verified trend trader opened a ~$30M BTC long at 20x.",
                                  facts_used=["position.notional_usd", "position.leverage"]), origin="ai")
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = now() + interval '1 hour' WHERE id = :a"), {"a": art})
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        d = c.get("/api/publish", params={"tab": "ready", "pair": "true"}, headers=h).json()
        assert [x["id"] for x in d["cards"]] == [art] and [x["id"] for x in d["siblings"]] == [en]
        assert d["siblings"][0]["status"] == "draft_ready" and d["siblings"][0]["facts_used"]
        assert c.get("/api/publish", params={"tab": "ready"}, headers=h).json()["siblings"] == []
    finally:
        app.dependency_overrides.clear()


def test_a_first_draft_past_its_shelf_life_is_not_written_and_old_proposals_close(sf):
    from datetime import timedelta

    from nr.desk.drafting import generate_draft
    from nr.publish.expiry import expire_due
    from tests.fakes import FakeProvider
    nr = newsroom(sf)
    eid = candidate_event(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        proposed = assignment.create(s, eid, nr["en"], source="eic_ai")
        s.commit()
    prov = FakeProvider()
    late = NOW + timedelta(days=1)
    assert generate_draft(sf, aid, late, transport=prov) == {"skipped": "the event is past its shelf life"}
    assert prov.requests == []  # nothing was paid for
    expire_due(sf, late)
    with sf() as s:
        assert s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar() == "expired"
        assert s.execute(text("SELECT status FROM assignment WHERE id = :a"), {"a": proposed}).scalar() == "cancelled"


def test_time_left_filter_plan_and_metrics_due_and_weekly_report(sf):
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = now() + interval '20 minutes' WHERE id = :a"), {"a": art})
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        assert [x["id"] for x in c.get("/api/publish", params={"within": 30}, headers=h).json()["cards"]] == [art]
        assert c.get("/api/publish", params={"within": 10}, headers=h).json()["cards"] == []
        from datetime import UTC, datetime
        at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        assert c.post(f"/api/articles/{art}/plan", json={"planned_at": at}, headers=h).status_code == 200
        assert c.get("/api/publish", headers=h).json()["cards"][0]["plan"]["status"] == "planned"
        assert c.post(f"/api/articles/{art}/publish", json={}, headers=h).status_code == 200
        with sf() as s:
            s.execute(text("UPDATE publication_record SET published_at = now() - interval '30 hours'"))
            s.commit()
        card = c.get("/api/publish", params={"tab": "published"}, headers=h).json()["cards"][0]
        assert card["metrics_due"] is not None
        rep = c.get("/api/reports/weekly", headers=h).json()
        assert rep["total"]["written"] == 1 and rep["total"]["published"] == 1
        assert rep["by_account"][0]["key"] == "alphascope_cn"
        assert c.post(f"/api/publications/{card['metrics_due']}/performance", headers=h,
                      json={"captured_at": at, "likes": 12, "impressions": 900}).status_code == 200
        assert c.get("/api/publish", params={"tab": "published"}, headers=h).json()["cards"][0]["metrics_due"] is None
        assert c.get("/api/reports/weekly", headers=h).json()["total"]["likes"] == 12
    finally:
        app.dependency_overrides.clear()


def test_a_rewrite_is_written_as_the_next_chapter(sf):
    from nr.desk.drafting import generate_draft
    from tests.fakes import FakeProvider
    from tests.helpers import draft_output
    nr, eid, art = drafted(sf)
    with sf() as s:
        aid = s.execute(text("SELECT assignment_id FROM article WHERE id = :a"), {"a": art}).scalar()
    prov = FakeProvider(draft_output("zh", "这头巨鲸又加了：约3000万美元 BTC 多单，20倍。",
                                     ["position.notional_usd", "position.leverage"]))
    generate_draft(sf, aid, NOW, transport=prov, sequel=True, review=False)
    assert "previous_draft" in prov.requests[0]["user"] and ZH in prov.requests[0]["user"]


def test_revise_by_review_hands_the_notes_to_the_editor(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        vid = s.execute(text("SELECT current_version_id FROM article WHERE id = :a"), {"a": art}).scalar()
        s.execute(text("""INSERT INTO article_review (article_version_id, verdict, scores, issues, summary, revision_note)
                          VALUES (:v, 'revise', '{}', '[{"quote": "x", "problem": "y", "fix": "z"}]', '开头太平', '第一句先抛反差')"""),
                  {"v": vid})
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        assert c.post(f"/api/articles/{art}/revise", headers=h).status_code == 200
    finally:
        app.dependency_overrides.clear()
    with sf() as s:
        p = s.execute(text("SELECT payload FROM job WHERE kind = 'draft_generate'")).scalar()
    assert p["mode"] == "polish" and p["review_note"] == "第一句先抛反差" and p["review_issues"][0]["fix"] == "z"


def test_each_post_has_one_plain_state_and_bulk_approve_moves_it_to_ready(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr, eid, art = drafted(sf)
    with sf() as s:
        s.execute(text("UPDATE article SET expires_at = now() + interval '1 hour' WHERE id = :a"), {"a": art})
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        d = c.get("/api/publish", params={"tab": "review"}, headers=h).json()
        assert [x["id"] for x in d["cards"]] == [art] and d["counts"]["review"] == 1 and d["counts"]["ready"] == 0
        assert d["cards"][0]["n_versions"] == 1 and d["cards"][0]["venue"] == "hyperliquid"
        assert c.get("/api/publish", params={"tab": "review", "venue": "okx"}, headers=h).json()["cards"] == []
        with sf() as s:  # the AI reviewer turns it down: a person is needed
            vid = s.execute(text("SELECT current_version_id FROM article WHERE id = :a"), {"a": art}).scalar()
            s.execute(text("""INSERT INTO article_review (article_version_id, verdict, scores, summary)
                              VALUES (:v, 'reject', '{}', 'x')"""), {"v": vid})
            s.commit()
        d = c.get("/api/publish", params={"tab": "attention"}, headers=h).json()
        assert d["cards"][0]["why"] == "rejected" and d["counts"]["review"] == 0
        r = c.post("/api/articles/bulk", json={"ids": [art], "action": "approve"}, headers=h).json()
        assert r["done"] == [art]
        assert [x["id"] for x in c.get("/api/publish", params={"tab": "ready"}, headers=h).json()["cards"]] == [art]
    finally:
        app.dependency_overrides.clear()


def test_a_stale_draft_is_rechecked_on_the_latest_facts_and_approved(sf):
    from nr.articles.versions import current_version
    nr, eid, art = drafted(sf)
    with sf() as s:
        before = current_version(s, art)
        s.execute(text("UPDATE article SET status = 'stale' WHERE id = :a"), {"a": art})
        pub.quick_approve(s, art, nr["chief"])
        s.commit()
        after = current_version(s, art)
        assert s.execute(text("SELECT status FROM article WHERE id = :a"), {"a": art}).scalar() == "approved"
    assert after["id"] != before["id"] and after["body"] == before["body"] and after["origin"] == before["origin"]


def test_the_platforms_running_figures_do_not_make_a_draft_stale():
    from nr.facts.builder import material_changes
    old = {"builder_version": "facts-3", "event": {}, "gate": {}, "facts": [
        {"id": "trader.binance_roi_30d", "kind": "return", "value": "1.31"},
        {"id": "history.binance_pnl_24h_usd", "kind": "realized_pnl", "value": "60000"},
        {"id": "close.realized_pnl_usd", "kind": "realized_pnl", "value": "40000"}]}
    new = {**old, "facts": [{**old["facts"][0], "value": "1.45"}, {**old["facts"][1], "value": "90000"}, old["facts"][2]]}
    assert material_changes(old, new) == []
