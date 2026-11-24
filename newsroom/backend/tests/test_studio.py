import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from nr.ai.providers.base import ChatRequest
from nr.ai.providers.openai_responses import body_for, parse_response, parse_stream
from nr.jobs.handlers import Deps, run_pending
from tests.fakes import FakeFeed, FakeProvider
from tests.helpers import NOW, candidate_event, draft_output, newsroom
from tests.test_api import client, login  # noqa: F401  (fixture)

ZH = "一个已验证趋势风格的地址开出约3000万美元 BTC 多单，杠杆20倍。"


def article(client, nr, eid, account="zh"):
    h = login(client, "chief")
    aid = client.post(f"/api/events/{eid}/assignments", json={"account_id": nr[account]}, headers=h).json()["assignment_id"]
    return h, aid, client.post(f"/api/assignments/{aid}/accept", headers=h).json()["article_id"]


def jobs(sf, provider):
    # jobs queued through the API are scheduled at the database's clock
    return run_pending(sf, Deps(feed=FakeFeed().client, provider=provider, now=lambda: datetime.now(UTC)),
                       ("draft_generate",))


def test_responses_protocol_shapes():
    cfg = {"model": "m", "json_mode": "json_schema", "extra_body": {"store": False}}
    b = body_for(cfg, ChatRequest(system="rules", user="data", max_tokens=100, json_schema={"type": "object"},
                                  schema_name="DraftOut"))
    assert b["instructions"] == "rules" and b["input"][0]["content"][0]["text"] == "data" and b["store"] is False
    assert b["text"]["format"]["type"] == "json_schema" and b["max_output_tokens"] == 100
    text_, usage, problem = parse_response({"status": "completed", "usage": {"input_tokens": 5, "output_tokens": 2},
                                            "output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}]})
    assert text_ == '{"ok":true}' and usage["input_tokens"] == 5 and problem is None
    assert parse_response({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})[2] == "truncated"
    final = parse_stream(['event: x', 'data: {"type": "response.output_text.delta", "delta": "{\\"ok\\""}',
                          'data: {"type": "response.output_text.delta", "delta": ": true}"}',
                          'data: {"type": "response.completed", "response": {"status": "completed", '
                          '"usage": {"input_tokens": 1, "output_tokens": 1}}}'])
    assert parse_response(final)[0] == '{"ok": true}'


def test_roles_compare_and_fix(client, sf):
    nr = newsroom(sf)
    eid = candidate_event(sf)
    h, aid, art = article(client, nr, eid)
    with sf() as s:
        assert s.execute(text("SELECT agent_key FROM assignment WHERE id = :a"), {"a": aid}).scalar() == "reporter_breaking"
        models = s.execute(text("SELECT id FROM ai_model_config ORDER BY id")).scalars().all()
    # one draft per model; none becomes current
    assert len(client.post(f"/api/articles/{art}/compare", json={"model_ids": models}, headers=h).json()["jobs"]) == 2
    prov = FakeProvider(draft_output("zh", ZH, ("position.notional_usd", "position.leverage")),
                        draft_output("zh", "巨鲸开出约3000万美元多单，杠杆20倍，已实现利润9000万美元。"))
    done = jobs(sf, prov)
    assert [d["result"]["made_current"] for d in done] == [False, False]
    assert "Your beat is breaking news" in prov.requests[0]["system"]
    assert {r["model"] for r in prov.requests} == {"model-primary", "model-fallback"}
    cmp = client.get(f"/api/articles/{art}/compare", headers=h).json()
    assert sorted(v["guard_status"] for v in cmp["versions"]) == ["fail", "pass"]
    assert all(v["cost_usd"] is not None and v["model"] for v in cmp["versions"])
    # use the failing one, then ask the fact checker to fix it
    bad = next(v for v in cmp["versions"] if v["guard_status"] == "fail")
    client.post(f"/api/articles/{art}/accept-version", json={"version_id": bad["id"]}, headers=h)
    client.post(f"/api/articles/{art}/ai", json={"mode": "fix"}, headers=h)
    prov2 = FakeProvider(draft_output("zh", ZH, ("position.notional_usd", "position.leverage")))
    out = jobs(sf, prov2)[0]["result"]
    assert out["agent"] == "fact_checker" and out["guard"] == "pass" and out["made_current"] is True
    data = prov2.requests[0]["user"]
    assert "guard_findings" in data and "PNL_CALLED_REALIZED" in data


def test_assignment_desk_calendar_and_rpa(client, sf):
    nr = newsroom(sf)
    eid = candidate_event(sf)
    h, aid, art = article(client, nr, eid)
    due = (NOW + timedelta(hours=2)).isoformat()
    assert client.patch(f"/api/assignments/{aid}", json={"claim": True, "due_at": due}, headers=h).status_code == 200
    row = client.get("/api/assignments", headers=h).json()[0]
    assert row["claimed_by"] == "chief" and row["article_id"] == art
    client.post(f"/api/articles/{art}/versions", json={"body": ZH, "facts_used": ["position.notional_usd"]}, headers=h)
    client.post(f"/api/articles/{art}/review", json={"action": "submit"}, headers=h)
    client.post(f"/api/articles/{art}/review", json={"action": "approve"}, headers=h)
    assert client.get("/api/plans", headers=h).json()["unscheduled"][0]["article_id"] == art
    t = NOW - timedelta(minutes=5)
    with sf() as s:  # another post on the account 20 minutes earlier: inside the 60-minute gap
        s.execute(text("""INSERT INTO publication_plan (article_id, account_id, planned_at, status)
                          VALUES (:a, :acc, :t, 'cancelled')"""), {"a": art, "acc": nr["zh"], "t": t})
        s.execute(text("UPDATE editorial_account SET rpa_enabled = true WHERE id = :a"), {"a": nr["zh"]})
        # the API runs on the real clock: keep the post in time (an expired post is never handed out)
        s.execute(text("UPDATE article SET expires_at = now() + interval '1 hour' WHERE id = :a"), {"a": art})
        s.commit()
    r = client.post(f"/api/articles/{art}/plan", json={"planned_at": t.isoformat()}, headers=h)
    assert r.status_code == 200
    # the RPA needs its own token
    assert client.get("/api/rpa/next").status_code == 401
    with sf() as s:
        s.execute(text("INSERT INTO user_role VALUES (:u, 'admin')"), {"u": nr["chief"]})
        s.commit()
    token = client.post("/api/settings/rpa-token", headers=h).json()["token"]
    rh = {"Authorization": f"Bearer {token}"}
    nxt = client.get("/api/rpa/next", headers=rh).json()["plan"]
    assert nxt["handle"] == "alphascope_cn" and "3000万美元" in nxt["text"]
    assert client.get("/api/rpa/next", headers=rh).json()["plan"] is None  # claimed
    res = client.post(f"/api/rpa/plans/{nxt['id']}/result", headers=rh,
                      json={"ok": True, "x_url": "https://x.com/alphascope_cn/status/1860000000000000999",
                            "published_at": NOW.isoformat()})
    assert res.status_code == 200, res.text
    with sf() as s:
        st = s.execute(text("SELECT status, publication_id IS NOT NULL FROM publication_plan WHERE id = :i"),
                       {"i": nxt["id"]}).one()
        assert st == ("done", True)
        assert s.execute(text("SELECT count(*) FROM audit_log WHERE action = 'rpa.published'")).scalar() == 1
    # public pages show it; nothing unpublished leaks
    stories = client.get("/api/public/stories").json()
    assert len(stories) == 1 and stories[0]["name"].startswith("0xabab")
    story = client.get(f"/api/public/stories/{stories[0]['id']}").json()
    ch = story["chapters"][0]
    assert ch["posts"][0]["x_url"].endswith("0999") and {f["id"] for f in ch["facts"]} <= {
        "position.notional_usd", "change.notional_usd", "position.leverage", "position.unrealized_pnl_usd",
        "close.pnl_estimate_usd", "liquidation.closed_pnl_usd", "change.window", "episode.first_seen_at"}
    assert "body" not in json.dumps(story) or "headline" not in json.dumps(story)
    assert client.get("/api/public/ledger").json()[0]["position_now"] == "open"


def test_public_pages_hide_unpublished_work(client, sf):
    newsroom(sf)
    candidate_event(sf)
    assert client.get("/api/public/stories").json() == []
    assert client.get("/api/public/ledger").json() == []
    with sf() as s:
        sid = s.execute(text("SELECT id FROM news_story")).scalar()
    assert client.get(f"/api/public/stories/{sid}").status_code == 404


def test_style_versions_and_columns_api(client, sf):
    nr = newsroom(sf)  # noqa: F841
    h = login(client, "chief")
    with sf() as s:
        assert s.execute(text("SELECT count(DISTINCT key) FROM editorial_style")).scalar() == 40  # 2 seeds + 12 x 2 library + 7 x 2 house
    r = client.post("/api/styles", headers=h, json={"key": "zh_breaking", "language": "zh", "name": "突发快讯 v2",
                                                    "guide": "更短：两句以内，先写事实，再写风险。", "forbidden": ["重磅"]})
    assert r.json()["version"] == 2
    cols = {c["key"]: c for c in client.get("/api/columns", headers=h).json()}
    assert {"copy_vs_board", "board_dropouts", "liq_ladder", "forecast_ledger", "funding_divergence",
            "holding_profile", "style_changes", "weekly_scorecard", "long_short_duel", "streak_breaker", "recap",
            "dossier"} == set(cols)
    assert client.post("/api/columns/dossier/run", json={"params": {}}, headers=h).status_code == 422
    assert client.post("/api/columns/dossier/run", json={"params": {"trader_key": "0x" + "ab" * 20}},
                       headers=h).status_code == 200
