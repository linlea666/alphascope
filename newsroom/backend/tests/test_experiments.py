"""Contrast A/B experiments, the v2 reporter prompt with story memory, and ratings of draft versions."""

import json
from datetime import timedelta

from sqlalchemy import text

from nr.articles.versions import Draft, save_version
from nr.desk import assignment
from nr.desk.drafting import generate_draft
from nr.experiments import contrast_ab
from nr.jobs.handlers import Deps, run_pending
from nr.publish import service as pub
from tests.fakes import OTHER, WHALE, FakeFeed, context, ev, position
from tests.helpers import NOW, draft_output, make_user, newsroom
from tests.pipeline import pipeline
from tests.test_api import client, login  # noqa: F401  (fixture)
from tests.test_contrast import crowd

ZH = "样本中40个地址持有BTC多单，这笔3000万美元多单杠杆20倍。"


class Reporter:
    """Answers v2 prompts with the v2 fields and v1 prompts without them; keeps every request."""

    def __init__(self):
        self.requests: list[dict] = []

    def __call__(self, cfg, req):
        from nr.ai.providers.base import ChatResult
        self.requests.append({"system": req.system, "user": req.user})
        out = draft_output("zh", ZH, ["position.notional_usd", "position.leverage"])
        if "Structure (version 2)" in req.system:
            out = {**out, "facts_used": out["facts_used"] + ["crowd.same_side_traders"],
                   "lede_fact_ids": ["crowd.same_side_traders"], "why_now": "第二大的BTC多单刚出现",
                   "open_question": "他会在91,000美元前减仓吗？"}
        return ChatResult(text=json.dumps(out, ensure_ascii=False), input_tokens=1000 if "version 2" in req.system
                          else 800, output_tokens=300, stop_reason="end", http_status=200, latency_ms=5,
                          model=cfg["model"])


def data_of(request: dict) -> dict:
    """The JSON the runner put between the <data> tags."""
    body = request["user"].split("<data>", 1)[1].split("</data>", 1)[0]
    return json.loads(body)


def two_events(sf) -> FakeFeed:
    feed = FakeFeed()
    feed.events = [ev(1, "open", 0, 300, 100000, entry=98000), ev(2, "open", 0, 280, 100000, trader=OTHER)]
    for t, szi in ((WHALE, 300), (OTHER, 280)):
        feed.contexts[t] = context(trader=t, positions=[position(szi=szi, mark=100000, entry=98000, lev=20, liq=91000)])
    feed.research["/market/coin-positions"] = crowd()
    pipeline(sf, feed, NOW)
    return feed


def test_experiment_runs_both_arms_blind_and_scores_them(sf):
    nr = newsroom(sf)
    feed = two_events(sf)
    with sf() as s:
        s.execute(text("UPDATE ai_agent_profile SET fallback_model_config_id = NULL"))
        model = s.execute(text("SELECT id FROM ai_model_config WHERE name = 'primary'")).scalar()
        s.commit()
    out = contrast_ab.create(sf, feed.client(), "contrast-1", nr["zh"], model, 4, NOW, nr["chief"], seed=1,
                             arms=contrast_ab.LEGACY_ARMS)
    assert out["pairs"] == 2 and out["by_stratum"]["open_add"] == 2  # thin strata are filled from any class
    rep = Reporter()
    run_pending(sf, Deps(feed=feed.client, provider=rep, now=lambda: NOW), ("draft_generate",))
    with sf() as s:
        pairs = [dict(r) for r in s.execute(text("""
            SELECT x.*, va.prompt_ref AS a_prompt, vb.prompt_ref AS b_prompt, vb.lede_fact_ids, vb.open_question,
                   e.state FROM experiment_pair x JOIN article_version va ON va.id = x.a_version_id
            JOIN article_version vb ON vb.id = x.b_version_id JOIN news_event e ON e.id = x.news_event_id
            ORDER BY x.id""")).mappings()]
    assert len(pairs) == 2 and all(p["a_prompt"].endswith("@v1") and p["b_prompt"].endswith("@v2") for p in pairs)
    assert pairs[0]["lede_fact_ids"] == ["crowd.same_side_traders"] and pairs[0]["open_question"].startswith("他会")
    assert {p["state"] for p in pairs} == {"candidate"}  # trials never move the event
    arm_a = [r for r in rep.requests if "Structure (version 2)" not in r["system"]]
    arm_b = [r for r in rep.requests if "Structure (version 2)" in r["system"]]
    ids_a = {f["id"] for f in data_of(arm_a[0])["fact_packet"]["facts"]}
    ids_b = {f["id"] for f in data_of(arm_b[0])["fact_packet"]["facts"]}
    assert not any(i.startswith(("crowd.", "book.", "radar.", "contrast.")) for i in ids_a)
    assert "crowd.same_side_traders" in ids_b and "lede_fact_ids" in arm_b[0]["user"]  # the v2 schema
    assert "lede_fact_ids" not in arm_a[0]["user"]
    # blind review: slots hide the arms; a vote for a slot lands on the arm behind it
    with sf() as s:
        first = contrast_ab.next_pair(s, "contrast-1", nr["chief"])
        assert [d["slot"] for d in first["drafts"]] == ["1", "2"] and "a_first" not in first
        p = next(x for x in pairs if x["id"] == first["pair_id"])
        b_slot = "1" if not p["a_first"] else "2"
        good, poor = {"information": 5, "lede": 4, "evidence": 4, "publishable": 4}, \
            {"information": 2, "lede": 2, "evidence": 3, "publishable": 2}
        contrast_ab.vote(s, "contrast-1", first["pair_id"], nr["chief"], b_slot, [],
                         {b_slot: good, "2" if b_slot == "1" else "1": poor}, "")
        s.commit()
        second = contrast_ab.next_pair(s, "contrast-1", nr["chief"])
        assert second["pair_id"] != first["pair_id"]
        contrast_ab.vote(s, "contrast-1", second["pair_id"], nr["chief"], "tie", ["1"],
                         {"1": poor, "2": poor}, "")
        s.commit()
        assert contrast_ab.next_pair(s, "contrast-1", nr["chief"]) is None
        r = contrast_ab.results(s, "contrast-1")
        ratings = s.execute(text("SELECT count(*), bool_and(blind) FROM draft_rating")).one()
    assert r["votes"] == 2 and r["b_win_rate"] == 0.75 and r["arms"]["b"]["mean_scores"]["information"] == 3.5
    assert r["arms"]["a"]["misleading"] + r["arms"]["b"]["misleading"] == 1
    assert r["contrast_coverage"] == 1.0 and r["families"]["crowd"] == 2
    assert r["extra_tokens_b"] == round(1300 / 1100 - 1, 3) and r["checks"]["b_win_rate"] is True
    assert ratings == (4, True)


def test_story_memory_reaches_the_next_v2_draft(sf):
    nr = newsroom(sf)
    feed = two_events(sf)
    with sf() as s:
        first = s.execute(text("SELECT id, story_id FROM news_event WHERE source_event_count = 1 ORDER BY id")).first()
        aid = assignment.create(s, first[0], nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        save_version(s, art, Draft(body=ZH, facts_used=["position.notional_usd"], open_question="会在91,000美元前减仓吗？"),
                     origin="human", user_id=nr["chief"])
        pub.submit(s, art, nr["chief"])
        pub.approve(s, art, nr["chief"])
        pub.mark_published(s, art, nr["chief"], "https://x.com/alphascope_cn/status/1860000000000000001",
                           NOW - timedelta(minutes=1), NOW)
        s.commit()
    # the same position grows: a follow-up event in the same story, written with the v2 prompt
    feed.events.append(ev(3, "increase", 300, 700, 100000))
    feed.contexts[WHALE] = context(positions=[position(szi=700, mark=100000, entry=98000, lev=20, liq=91000)])
    pipeline(sf, feed, NOW + timedelta(minutes=30))
    with sf() as s:
        follow = s.execute(text("SELECT id FROM news_event WHERE story_id = :s AND id <> :e ORDER BY id DESC"),
                           {"s": first[1], "e": first[0]}).scalar()
        aid = assignment.create(s, follow, nr["zh"], source="trial", status="accepted", user_id=nr["chief"])
        assignment.accept(s, aid, nr["chief"])
        s.commit()
    rep = Reporter()
    generate_draft(sf, aid, NOW, transport=rep, prompt_version=2)
    memory = data_of(rep.requests[0])["story_memory"]
    assert memory["published"][0]["text"] == ZH and memory["open_questions"] == ["会在91,000美元前减仓吗？"]
    assert any(c["id"] == "position.size" for c in memory["changed_since_last_post"])
    generate_draft(sf, aid, NOW, transport=rep, prompt_version=1)  # v1 prompts never get the memory
    assert "story_memory" not in data_of(rep.requests[1])


def test_experiment_api_and_ratings(client, sf):
    nr = newsroom(sf)
    with sf() as s:
        make_user(s, "rev", ["reviewer"])
        model = s.execute(text("SELECT id FROM ai_model_config WHERE name = 'primary'")).scalar()
        s.commit()
    h, rh = login(client, "chief"), login(client, "rev")
    body = {"name": "contrast-api", "account_id": nr["zh"], "model_config_id": model, "n": 10}
    assert client.post("/api/experiments", json=body, headers=rh).status_code == 403
    assert client.post("/api/experiments", json=body, headers=h).json()["name"] == "contrast-api"
    assert client.post("/api/experiments", json=body, headers=h).json()["name"] == "contrast-api-2"  # never a clash
    auto = client.post("/api/experiments", json={**body, "name": None, "b": {"prompt_version": 3, "style_key": "zh_degen"}},
                       headers=h).json()["name"]
    assert auto.startswith("x-") and auto.endswith("model-primary")
    with sf() as s:
        s.execute(text("UPDATE ai_model_config SET last_test_ok = false, last_test_detail = '{\"detail\": \"HTTP 403: Cloudflare\"}'"))
        s.commit()
    r = client.post("/api/experiments", json=body, headers=h)
    assert r.status_code == 422 and "failed its last test: HTTP 403" in r.json()["detail"]
    with sf() as s:
        assert s.execute(text("SELECT payload->>'name' FROM job WHERE kind = 'experiment_create'")).scalar() == "contrast-api"
    assert "contrast-api" in [p["experiment"] for p in client.get("/api/experiments", headers=rh).json()["pending"]]
    assert client.get("/api/experiments/nope/next", headers=rh).status_code == 404
    # ratings of an ordinary version
    feed = two_events(sf)
    with sf() as s:
        eid = s.execute(text("SELECT min(id) FROM news_event")).scalar()
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        art = assignment.accept(s, aid, nr["chief"])
        vid = save_version(s, art, Draft(body=ZH, facts_used=["position.notional_usd"]), origin="human",
                           user_id=nr["chief"])["id"]
        s.commit()
    url = f"/api/articles/{art}/versions/{vid}/rating"
    assert client.put(url, json={"score": 4, "tags": ["lede"]}, headers=rh).status_code == 200
    assert client.put(url, json={"score": 2, "tags": ["clickbait"]}, headers=rh).status_code == 422
    assert client.put(url, json={"score": 3, "tags": []}, headers=rh).status_code == 200  # one rating per person
    got = client.get(f"/api/articles/{art}/ratings", headers=h).json()
    assert len(got) == 1 and got[0]["score"] == 3 and got[0]["mine"] is False
    assert feed  # keeps the fake feed's events alive for the pipeline above


def test_sampling_is_not_starved_by_the_hourly_context_budget(sf):
    nr = newsroom(sf)
    feed = two_events(sf)
    with sf() as s:
        model = s.execute(text("SELECT id FROM ai_model_config WHERE name = 'primary'")).scalar()
        # the radar has used up this hour's context calls, and the packets are old enough to need a refresh
        s.execute(text("""INSERT INTO job (kind, status, finished_at) SELECT 'build_facts', 'done', :n
                          FROM generate_series(1, 300)"""), {"n": NOW})
        s.execute(text("UPDATE news_event SET facts_refreshed_at = :t"), {"t": NOW - timedelta(hours=2)})
        s.commit()
    out = contrast_ab.create(sf, feed.client(), "busy-hour", nr["zh"], model, 4, NOW, nr["chief"], seed=1)
    assert out["pairs"] == 2 and out["skipped"] == {}
    empty = contrast_ab.create(sf, feed.client(), "busy-hour", nr["zh"], model, 4, NOW, nr["chief"])
    assert empty["pairs"] == 0 and empty["skipped"] == {"no_recent_events": 1}  # both events are already in it
