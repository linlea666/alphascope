"""Failing less: a fallback known not to work is skipped, a model that keeps hitting gateway errors sits out, and ids
that aren't facts are dropped instead of costing a rewrite."""

from sqlalchemy import text

from nr.ai.providers.base import ProviderError
from nr.desk import assignment
from nr.desk.drafting import generate_draft
from tests.fakes import FakeProvider
from tests.helpers import NOW, candidate_event, draft_output, newsroom

BLOCKED = ProviderError("http_error", "HTTP 403: Attention Required! | Cloudflare")
ZH = "一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。"


def article(sf, nr):
    eid = candidate_event(sf)
    with sf() as s:
        aid = assignment.create(s, eid, nr["zh"], source="editor", status="accepted", user_id=nr["chief"])
        assignment.accept(s, aid, nr["chief"])
        s.commit()
    return aid


def models_called(sf):
    with sf() as s:
        return list(s.execute(text("SELECT model FROM ai_call_log ORDER BY id")).scalars())


def test_a_fallback_that_failed_its_test_is_not_tried(sf):
    nr = newsroom(sf)
    aid = article(sf, nr)
    with sf() as s:
        s.execute(text("UPDATE ai_model_config SET last_test_ok = false WHERE name = 'fallback'"))
        s.commit()
    prov = FakeProvider(ProviderError("http_error", "HTTP 400: bad request"))
    try:
        generate_draft(sf, aid, NOW, transport=prov)
    except Exception:  # noqa: BLE001 - the primary failed and nothing else was tried
        pass
    assert models_called(sf) == ["model-primary"]


def test_a_model_with_three_gateway_errors_sits_out_and_the_fallback_writes(sf):
    nr = newsroom(sf)
    aid = article(sf, nr)
    with sf() as s:
        mid = s.execute(text("SELECT id FROM ai_model_config WHERE name = 'primary'")).scalar()
        for _ in range(3):
            s.execute(text("""INSERT INTO ai_call_log (agent_key, prompt_ref, model_config_id, protocol, model, purpose, attempt,
                                                      fallback_used, status, error, created_at)
                              VALUES ('reporter', 'reporter@v1', :m, 'openai_chat', 'model-primary', 'draft_generate', 1,
                                      false, 'http_error', 'HTTP 403: Cloudflare', :n)"""), {"m": mid, "n": NOW})
        s.commit()
    generate_draft(sf, aid, NOW, transport=FakeProvider(draft_output("zh", ZH, ["position.notional_usd"])))
    assert models_called(sf)[3:] == ["model-fallback"]  # the primary was skipped this hour


def test_ids_that_are_not_facts_are_dropped_not_rewritten(sf):
    nr = newsroom(sf)
    aid = article(sf, nr)
    out = draft_output("zh", ZH, ["position.notional_usd", "event.class", "OBSERVED_SAMPLE"])
    prov = FakeProvider(out)
    generate_draft(sf, aid, NOW, transport=prov)
    with sf() as s:
        v = s.execute(text("SELECT facts_used, guard_status FROM article_version")).one()
    assert len(prov.requests) == 1 and v[0] == ["position.notional_usd"]


def test_fixes_name_the_blocked_model_and_one_click_replaces_it(sf):
    from fastapi.testclient import TestClient

    from nr.api.admin import ai_fixes
    from nr.db import get_session
    from nr.main import app
    from tests.test_api import login
    newsroom(sf)
    with sf() as s:
        ids = dict(s.execute(text("SELECT name, id FROM ai_model_config")).all())
        s.execute(text("UPDATE ai_model_config SET last_test_ok = (name = 'primary')"))
        s.execute(text("UPDATE ai_agent_profile SET prompt_version = 1"))
        s.execute(text("INSERT INTO user_role SELECT id, 'admin' FROM app_user WHERE username = 'chief'"))
        s.commit()
        fixes = {f["kind"]: f for f in ai_fixes(s, NOW)}
    fix = fixes["replace_model"]
    assert fix["model"]["id"] == ids["fallback"] and fix["reason"] == "test_failed" and fix["primary_in"] == []
    assert fix["suggest"]["id"] == ids["primary"] and "reporter" in fix["fallback_in"]
    assert "reporter" in fixes["prompts_behind"]["agents"]

    def override():
        with sf() as s:
            yield s
    app.dependency_overrides[get_session] = override
    try:
        c = TestClient(app)
        h = login(c, "chief")
        r = c.post("/api/ai/agents/replace-model", json={"from_model_id": ids["fallback"], "to_model_id": ids["primary"]},
                   headers=h)
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
    with sf() as s:
        # the fallback would equal the role's own model, so it is dropped rather than duplicated
        assert s.execute(text("SELECT count(*) FROM ai_agent_profile WHERE active AND fallback_model_config_id IS NOT NULL")).scalar() == 0
        assert [f["kind"] for f in ai_fixes(s, NOW)] == ["prompts_behind"]


def test_a_thread_that_repeats_the_body_is_dropped(sf):
    nr = newsroom(sf)
    aid = article(sf, nr)
    out = draft_output("zh", ZH, ["position.notional_usd", "position.leverage"])
    out["thread"] = [ZH + "\n", "第二条：他的开仓均价约98000美元。"]
    generate_draft(sf, aid, NOW, transport=FakeProvider(out))
    with sf() as s:
        thread = s.execute(text("SELECT thread FROM article_version ORDER BY id DESC LIMIT 1")).scalar()
    assert thread == ["第二条：他的开仓均价约98000美元。"]


def test_a_draft_the_fact_check_stops_gets_one_fix_then_waits_for_a_person(sf):
    nr = newsroom(sf)
    aid = article(sf, nr)
    wrong = draft_output("zh", "一个已验证趋势风格的地址开出约9000万美元 BTC 多单，杠杆20倍。",
                         ["position.notional_usd", "position.leverage"])
    generate_draft(sf, aid, NOW, transport=FakeProvider(wrong))
    with sf() as s:
        jobs = s.execute(text("SELECT payload->>'mode' FROM job WHERE kind = 'draft_generate'")).scalars().all()
    assert jobs == ["fix"]  # one go for the fact checker
    generate_draft(sf, aid, NOW, mode="fix", transport=FakeProvider(wrong))
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM job WHERE kind = 'draft_generate'")).scalar() == 1  # no second fix
        assert s.execute(text("SELECT count(*) FROM job WHERE kind = 'draft_review'")).scalar() == 0


def test_failures_before_a_passing_test_no_longer_flag_the_model(sf):
    from datetime import timedelta

    from nr.api.admin import ai_fixes
    newsroom(sf)
    with sf() as s:
        mid = s.execute(text("SELECT id FROM ai_model_config WHERE name = 'fallback'")).scalar()
        for _ in range(3):
            s.execute(text("""INSERT INTO ai_call_log (agent_key, prompt_ref, model_config_id, protocol, model, purpose, attempt,
                                                      fallback_used, status, error, created_at)
                              VALUES ('reporter', 'reporter@v1', :m, 'openai_chat', 'model-fallback', 'draft_generate', 1,
                                      false, 'http_error', 'HTTP 403: Cloudflare', :n)"""), {"m": mid, "n": NOW - timedelta(hours=2)})
        s.execute(text("UPDATE ai_model_config SET last_test_ok = true, last_test_at = :n"), {"n": NOW - timedelta(hours=3)})
        s.commit()
        assert "replace_model" in [f["kind"] for f in ai_fixes(s, NOW)]  # blocked after its last good test
        s.execute(text("UPDATE ai_model_config SET last_test_at = :n"), {"n": NOW - timedelta(hours=1)})
        assert [f for f in ai_fixes(s, NOW) if f["kind"] == "replace_model"] == []  # fixed: the test passed since
