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
