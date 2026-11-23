from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from nr.db import get_session
from nr.main import app
from tests.helpers import NOW, candidate_event, make_user, newsroom

PW = "correct horse battery"
ZH = "一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。"


@pytest.fixture
def client(sf):
    def override():
        with sf() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client, username, password=PW) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_lockout(client, sf):
    with sf() as s:
        make_user(s, "ed", ["editor"])
        s.commit()
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events", headers={"Authorization": "Bearer nope"}).status_code == 401
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "ed", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ed", "password": PW}).status_code == 423


def test_account_permissions_are_isolated(client, sf):
    nr = newsroom(sf)
    eid = candidate_event(sf)
    with sf() as s:
        make_user(s, "rep_cn", ["reporter"], {nr["zh"]: ["view", "draft"]})
        make_user(s, "analyst", ["analyst"])
        s.commit()
    chief = login(client, "chief")
    arts = {}
    for acc in (nr["zh"], nr["en"]):
        aid = client.post(f"/api/events/{eid}/assignments", json={"account_id": acc}, headers=chief).json()["assignment_id"]
        arts[acc] = client.post(f"/api/assignments/{aid}/accept", headers=chief).json()["article_id"]
    rep, analyst = login(client, "rep_cn"), login(client, "analyst")
    body = {"body": ZH, "facts_used": ["position.notional_usd"]}
    assert client.post(f"/api/articles/{arts[nr['zh']]}/versions", json=body, headers=rep).status_code == 200
    assert client.post(f"/api/articles/{arts[nr['en']]}/versions", json=body, headers=rep).status_code == 403
    assert client.get(f"/api/articles/{arts[nr['en']]}", headers=rep).status_code == 403
    # a reporter drafts but never approves or publishes, even on their own account
    client.post(f"/api/articles/{arts[nr['zh']]}/review", json={"action": "submit"}, headers=rep)
    assert client.post(f"/api/articles/{arts[nr['zh']]}/review", json={"action": "approve"},
                       headers=rep).status_code == 403
    listed = client.get("/api/articles", headers=rep).json()["rows"]
    assert {r["account_id"] for r in listed} == {nr["zh"]}
    # analysts read everything and change nothing
    assert client.get(f"/api/articles/{arts[nr['en']]}", headers=analyst).status_code == 200
    assert client.post(f"/api/articles/{arts[nr['en']]}/versions", json=body, headers=analyst).status_code == 403
    assert client.post("/api/settings/emergency-stop", json={"active": True}, headers=analyst).status_code == 403
    assert client.post(f"/api/events/{eid}/evaluate", headers=rep).status_code == 403


def test_full_manual_flow_without_x_keys_or_ai(client, sf):
    nr = newsroom(sf, model=False)
    eid = candidate_event(sf)
    h = login(client, "chief")
    ev = client.get(f"/api/events/{eid}", headers=h).json()
    assert ev["packet"]["content"]["gate"]["status"] == "pass" and ev["source_events"]
    aid = client.post(f"/api/events/{eid}/assignments", json={"account_id": nr["zh"], "angle": "交易员档案"},
                      headers=h).json()["assignment_id"]
    art = client.post(f"/api/assignments/{aid}/accept", headers=h).json()["article_id"]
    bad = client.post(f"/api/articles/{art}/guard", json={"body": "巨鲸已实现利润5000万美元"}, headers=h).json()
    assert bad["status"] == "fail"
    v = client.post(f"/api/articles/{art}/versions", json={"body": ZH, "headline": "巨鲸开多",
                                                           "facts_used": ["position.notional_usd", "position.leverage"]},
                    headers=h).json()
    assert v["guard_status"] == "pass", v["guard"]
    assert client.post(f"/api/articles/{art}/review", json={"action": "submit"}, headers=h).status_code == 200
    assert client.post(f"/api/articles/{art}/review", json={"action": "approve"}, headers=h).status_code == 200
    rendered = client.post("/api/assets/render", json={"fact_packet_id": ev["packet"]["id"], "template": "position_card",
                                                       "language": "zh"}, headers=h).json()
    png = client.get(f"/api/assets/{rendered['assets']['card_png']}/file", headers=h)
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.post(f"/api/articles/{art}/copied", headers=h).json()["blockers"] == []
    url = "https://x.com/alphascope_cn/status/1860000000000000123"
    r = client.post(f"/api/articles/{art}/publish", json={"x_url": url, "published_at": NOW.isoformat()}, headers=h)
    assert r.status_code == 200, r.text
    pid = r.json()["publication_id"]
    assert client.post(f"/api/publications/{pid}/performance", json={"captured_at": (NOW + timedelta(hours=6)).isoformat(),
                                                                      "impressions": 5400, "likes": 41},
                       headers=h).status_code == 200
    pubs = client.get("/api/publications", headers=h).json()
    assert pubs[0]["x_post_id"] == "1860000000000000123" and pubs[0]["latest_performance"]["impressions"] == 5400
    story = client.get(f"/api/stories/{ev['event']['story_id']}", headers=h).json()
    assert story["timeline"][0]["publications"][0]["url"] == url and story["timeline"][0]["pinned_version"] == 1
    dash = client.get("/api/dashboard", headers=h).json()
    assert dash["accounts"][0]["published_today"] in (0, 1)
    with sf() as s:
        actions = s.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
    assert {"article.copy_text", "publication.record", "article.approve"} <= set(actions)
    assert client.post(f"/api/publications/{pid}/retract", json={"reason": "wrong coin"}, headers=h).status_code == 200


def test_reading_works_while_ai_is_down(client, sf, monkeypatch):
    nr = newsroom(sf)
    eid = candidate_event(sf)
    import nr.ai.runner as runner
    from nr.ai.providers.base import ProviderError

    def down(cfg, req):
        raise ProviderError("http_error", "connection refused", retryable=True)

    monkeypatch.setattr(runner, "default_transport", down)
    h = login(client, "chief")
    assert client.post(f"/api/events/{eid}/evaluate", headers=h).status_code == 200
    from nr.jobs.handlers import Deps, run_pending
    from tests.fakes import FakeFeed
    out = run_pending(sf, Deps(feed=FakeFeed().client, now=lambda: NOW), ("eic_evaluate",))
    assert "error" in out[0]
    assert client.get("/api/events", headers=h).json()["total"] == 1
    assert client.get(f"/api/events/{eid}", headers=h).json()["event"]["state"] == "candidate"
    assert client.get("/api/dashboard", headers=h).json()["ai"]["queued"] >= 1  # waiting for a retry
    assert client.get("/api/ai/costs", headers=h).json()["by_status"][0]["status"] == "http_error"


def test_model_test_never_echoes_the_key(client, sf, monkeypatch):
    newsroom(sf, model=False)
    with sf() as s:
        make_user(s, "root", ["admin"])
        s.commit()
    h = login(client, "root")
    mid = client.post("/api/ai/models", json={"name": "gw", "protocol": "anthropic_messages",
                                              "base_url": "https://www.aicodemirror.com/api/claudecode",
                                              "model": "claude-sonnet-5-5", "api_key": "sk-secret-123",
                                              "json_mode": "none"}, headers=h).json()["id"]
    import nr.ai.model_test as mt
    from nr.ai.providers.base import ChatResult, ProviderError

    def echo(cfg, req):
        nonce = req.user.split('"echo": "')[1].split('"')[0]
        return ChatResult(f'{{"ok": true, "echo": "{nonce}"}}', 20, 10, "end_turn", 200, 120, cfg["model"])

    monkeypatch.setattr(mt, "default_transport", echo)
    r = client.post(f"/api/ai/models/{mid}/test", headers=h).json()
    assert r["ok"] is True and r["stage"] == "done"
    monkeypatch.setattr(mt, "default_transport",
                        lambda cfg, req: (_ for _ in ()).throw(ProviderError("http_error", f"bad key {cfg['api_key']}")))
    r = client.post(f"/api/ai/models/{mid}/test", headers=h).json()
    assert r["ok"] is False and "sk-secret-123" not in str(r)
    listed = client.get("/api/ai/models", headers=h).json()
    assert listed[0]["has_key"] is True and "sk-secret-123" not in str(listed) and "api_key_secret" not in listed[0]


def test_ai_status_names_unbound_roles_and_one_click_binds_them(client, sf):
    """The production failure mode: models were added but no role selected one, so every AI job waited silently."""
    newsroom(sf, model=False)
    with sf() as s:
        make_user(s, "root", ["admin"])
        make_user(s, "ana", ["analyst"])
        s.commit()
    h, ana = login(client, "root"), login(client, "ana")
    mid = client.post("/api/ai/models", json={"name": "ds", "protocol": "openai_chat", "base_url": "https://api.deepseek.com",
                                              "model": "deepseek-flash", "api_key": "sk-x", "json_mode": "json_object"},
                      headers=h).json()["id"]
    st = client.get("/api/ai/status", headers=ana).json()
    assert st["core_unbound"] == ["eic", "reporter"] and "sk-x" not in str(st) and "base_url" not in str(st)
    client.put("/api/ai/agents/eic", json={"model_config_id": mid}, headers=h)
    assert client.post("/api/ai/agents/bind", json={"model_config_id": mid}, headers=ana).status_code == 403
    changed = client.post("/api/ai/agents/bind", json={"model_config_id": mid, "fallback_model_config_id": None},
                          headers=h).json()["changed"]
    assert "eic" not in changed and "reporter" in changed  # only_unbound leaves a deliberate choice alone
    st = client.get("/api/ai/status", headers=ana).json()
    assert st["core_unbound"] == [] and st["unbound"] == []
    agents = [a for a in client.get("/api/ai/agents", headers=h).json() if a["active"]]
    assert {a["model_config_id"] for a in agents} == {mid}
    eic = [a for a in client.get("/api/ai/agents", headers=h).json() if a["agent_key"] == "eic"]
    assert sorted(a["version"] for a in eic) == [1, 2]  # versioned, the old profile is kept
    assert client.post("/api/ai/agents/bind", json={"model_config_id": 999}, headers=h).status_code == 422


def test_prompt_versions_switch_in_bulk_and_only_unused_ones_can_be_deleted(client, sf):
    from tests.helpers import newsroom
    newsroom(sf)
    with sf() as s:
        s.execute(text("UPDATE user_role SET role = 'admin' WHERE user_id = (SELECT id FROM app_user WHERE username = 'chief')"))
        model = s.execute(text("SELECT model_config_id FROM ai_agent_profile WHERE agent_key = 'reporter'")).scalar()
        s.commit()
    h = login(client, "chief")
    r = client.post("/api/ai/agents/prompt-version", json={"prompt_version": 4,
                                                           "agent_keys": ["reporter", "reporter_breaking", "editor_zh"]}, headers=h)
    assert r.json()["changed"] == ["reporter", "reporter_breaking"]
    assert r.json()["skipped"] == [{"agent": "editor_zh", "reason": "no v4; available: v1, v2, v3"}]
    newest = client.post("/api/ai/agents/prompt-version", json={"agent_keys": ["eic", "editor_en"]}, headers=h).json()
    assert newest["changed"] == ["eic", "editor_en"]  # no version given: each role's newest
    with sf() as s:
        cur = s.execute(text("""SELECT prompt_version, model_config_id, max_output_tokens FROM ai_agent_profile
                                WHERE agent_key = 'reporter' AND active""")).one()
    assert cur == (4, model, 2500)  # nothing else about the role changed
    bad = client.put("/api/ai/agents/eic", json={"prompt_version": 4}, headers=h)
    assert bad.status_code == 422 and "available: v1, v2, v3" in bad.json()["detail"]
    assert client.put("/api/ai/agents/eic", json={"max_output_tokens": 3000}, headers=h).status_code == 200
    with sf() as s:
        assert s.execute(text("SELECT model_config_id FROM ai_agent_profile WHERE agent_key = 'eic' AND active")).scalar() == model
    # a new version nobody used can go; a seed or a version a role has pointed at stays
    v = client.post("/api/ai/prompts", json={"key": "reporter", "system_text": "x" * 30, "user_template": "write $language"},
                    headers=h).json()["version"]
    assert client.post("/api/ai/prompts", json={"key": "nobody", "system_text": "x" * 30, "user_template": "write it"},
                       headers=h).status_code == 422
    listed = {(p["key"], p["version"], p["language"]): p for p in client.get("/api/ai/prompts", headers=h).json()}
    assert listed[("reporter", v, None)]["deletable"] and not listed[("reporter", 4, "zh")]["deletable"]
    assert listed[("reporter", 4, "zh")]["active_for"] == ["reporter"]
    assert client.delete("/api/ai/prompts/reporter/4?language=zh", headers=h).status_code == 409
    assert client.delete(f"/api/ai/prompts/reporter/{v}", headers=h).status_code == 200
    with sf() as s, pytest.raises(Exception, match="has been used"):
        s.execute(text("DELETE FROM prompt_template WHERE key = 'reporter' AND version = 4"))
