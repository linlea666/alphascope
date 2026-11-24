"""Learning from the reviewer and the readers: candidate examples (placeholders, adoption makes a style version) and
the prompt coach (the facts part untouched, one version per role, an experiment, adoption switches the role)."""

import json
from datetime import timedelta

from sqlalchemy import text

from nr.articles.versions import Draft, save_version
from nr.desk import assignment, examples
from tests.fakes import FakeProvider
from tests.helpers import NOW, candidate_event, newsroom

ZH = "一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。"


def article_with_versions(sf, nr, n=1, agent="reporter_breaking", ref="reporter_breaking@v5.zh", style="zh_whale_alert"):
    eid = candidate_event(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        vids = []
        for _ in range(n):
            v = save_version(s, art, Draft(body=ZH, facts_used=["position.notional_usd"], style_key=style), origin="ai",
                             agent_key=agent, prompt_ref=ref, model="model-primary")
            vids.append(v["id"])
        s.commit()
    return art, vids


def test_templatize_keeps_the_shape_not_the_facts():
    out = examples.templatize("熬鹰刚开了约 111万美元的 $ETH 空单，20 倍杠杆；大表哥 7.8 倍。", ["大表哥", "熬鹰"])
    assert out == "{昵称}刚开了约 {数字}万美元的 $ETH 空单，{数字} 倍杠杆；{昵称2} {数字} 倍。"


def test_a_ready_post_becomes_a_candidate_and_adopting_it_makes_a_style_version(sf):
    nr = newsroom(sf)
    art, (vid,) = article_with_versions(sf, nr)
    with sf() as s:
        assert examples.from_review(s, vid, "revise", {"publishable": 5}) is None
        cid = examples.from_review(s, vid, "approve", {"publishable": 4})
        before = s.execute(text("SELECT max(version) FROM editorial_style WHERE key = 'zh_whale_alert'")).scalar()
        version = examples.adopt(s, cid, nr["chief"])
        s.commit()
        ex = s.execute(text("SELECT examples FROM editorial_style WHERE key = 'zh_whale_alert' AND version = :v"),
                       {"v": version}).scalar()
    assert version == before + 1 and "{数字}" in ex[-1] and "3000" not in ex[-1]


def test_a_post_readers_liked_more_than_usual_becomes_a_candidate(sf):
    from nr.publish import service as pub
    nr = newsroom(sf)
    art, (vid,) = article_with_versions(sf, nr)
    with sf() as s:
        pub.quick_approve(s, art, nr["chief"])
        s.execute(text("UPDATE article SET expires_at = :x WHERE id = :a"), {"x": NOW + timedelta(hours=1), "a": art})
        first = pub.mark_published(s, art, nr["chief"], None, NOW, NOW)
        pids = [first] + [s.execute(text("""  -- the account's other posts this month (copies of the record)
            INSERT INTO publication_record (article_id, article_version_id, account_id, published_at, recorded_by, final_text,
                                            final_text_hash, matches_version, guard_report, channel)
            SELECT article_id, article_version_id, account_id, published_at, recorded_by, final_text, final_text_hash,
                   matches_version, guard_report, channel FROM publication_record WHERE id = :p RETURNING id"""),
            {"p": first}).scalar_one() for _ in range(4)]
        s.execute(text("UPDATE publication_record SET published_at = now() - interval '1 day'"))
        for i, pid in enumerate(pids):
            s.execute(text("""INSERT INTO performance_snapshot (publication_id, captured_at, likes, source)
                              VALUES (:p, now(), :l, 'manual')"""), {"p": pid, "l": 100 if i == 4 else 10})
        s.commit()
    with sf() as s:
        assert examples.from_metrics(s, pids[0]) is None  # an ordinary post
        assert examples.from_metrics(s, pids[4]) is not None
        assert s.execute(text("SELECT reason FROM style_example_candidate")).scalar() == "engagement"


def coach_out(body: str) -> dict:
    return {"rationale": "审稿反复指出开头数字太多", "changes": [{"problem": "数字挤在开头", "change": "第一行最多一个数字"}],
            "writing": body + "\n13. 第一行最多一个数字。"}


def test_the_coach_redrafts_only_the_writing_part_and_opens_an_experiment(sf):
    from nr.desk.coach import run, split
    nr = newsroom(sf)
    with sf() as s:
        s.execute(text("UPDATE ai_agent_profile SET prompt_version = 5 WHERE agent_key = 'reporter_breaking'"))
        base = s.execute(text("""SELECT system_text FROM prompt_template WHERE key = 'reporter_breaking' AND version = 5
                                 AND language = 'zh'""")).scalar()
        s.commit()
    facts, heading, body = split(base)
    art, vids = article_with_versions(sf, nr, n=5)
    with sf() as s:
        for v in vids:
            s.execute(text("""INSERT INTO article_review (article_version_id, verdict, scores, issues, summary, created_at)
                              VALUES (:v, 'revise', '{"publishable": 3, "hook": 2}', :i, '开头太平', now())"""),
                      {"v": v, "i": json.dumps([{"quote": "x", "problem": "数字挤在开头", "fix": "先说人"}], ensure_ascii=False)})
        s.commit()
    from datetime import UTC, datetime
    out = run(sf, None, datetime.now(UTC), transport=FakeProvider(coach_out(body)), only="reporter_breaking")
    assert len(out["proposals"]) == 1
    with sf() as s:
        p = s.execute(text("SELECT * FROM prompt_proposal")).mappings().one()
        new = s.execute(text("""SELECT system_text FROM prompt_template WHERE key = 'reporter_breaking' AND version = :v
                                AND language = 'zh'"""), {"v": p["version"]}).scalar()
        en_new, en_old = (s.execute(text("""SELECT system_text FROM prompt_template WHERE key = 'reporter_breaking'
                                            AND version = :v AND language = 'en'"""), {"v": v}).scalar()
                          for v in (p["version"], 5))
        assert new.startswith(facts + heading) and new.endswith("第一行最多一个数字。")  # the facts rules are untouched
        assert en_new == en_old  # the English text, not redrafted, keeps its current wording in the new version
        assert p["status"] == "testing" and p["experiment_name"].startswith("coach-reporter-breaking")
        job = s.execute(text("SELECT payload FROM job WHERE kind = 'experiment_create'")).scalar()
        assert job["agent_key"] == "reporter_breaking" and job["arms"] == {"a": {"prompt_version": 5},
                                                                           "b": {"prompt_version": p["version"]}}
    # a draft that drops a name the drafting code reads goes back to the coach
    bad = coach_out(body.replace("lede_fact_ids", "开头事实"))
    from nr.desk.coach import redraft, groups
    with sf() as s:
        s.execute(text("UPDATE prompt_proposal SET status = 'rejected'"))
        s.commit()
        g = groups(s, NOW - timedelta(days=30))
    import pytest
    from nr.ai.runner import AIFailed
    with pytest.raises(AIFailed):
        redraft(sf, next(x for x in g if x["agent_key"] == "reporter_breaking"), {"system_text": base, "user_template": ""},
                datetime.now(UTC), FakeProvider(bad, bad, bad))


def test_adopting_a_proposal_switches_the_role(sf):
    from fastapi.testclient import TestClient

    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    nr = newsroom(sf)
    with sf() as s:
        s.execute(text("INSERT INTO user_role VALUES (:u, 'admin')"), {"u": nr["chief"]})
        s.execute(text("""INSERT INTO prompt_template (key, version, language, system_text, user_template, sha256, notes)
                          SELECT key, 99, language, system_text, user_template, sha256, 'test' FROM prompt_template
                          WHERE key = 'reporter_breaking' AND version = 5"""))
        pid = s.execute(text("""INSERT INTO prompt_proposal (prompt_key, language, base_version, version, rationale)
                                VALUES ('reporter_breaking', 'zh', 5, 99, 'x') RETURNING id""")).scalar()
        s.commit()

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        assert c.post(f"/api/learning/proposals/{pid}/adopt", headers=h).json()["agents"] == ["reporter_breaking"]
        assert c.get("/api/learning/proposals", headers=h).json()[0]["status"] == "adopted"
    finally:
        app.dependency_overrides.clear()
    with sf() as s:
        assert s.execute(text("SELECT prompt_version FROM ai_agent_profile WHERE agent_key = 'reporter_breaking' AND active")
                         ).scalar() == 99
