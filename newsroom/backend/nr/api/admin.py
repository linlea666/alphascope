"""Dashboard, accounts and permissions, settings and the emergency stop, AI models/agents/prompts and costs, users,
audit log, jobs and data-source health."""

import hashlib
import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.ai.diagnose import diagnose
from nr.ai.model_test import test_model
from nr.api.common import now, one, rows
from nr.auth.deps import active_user, require_roles, visible_accounts
from nr.auth.passwords import hash_password, password_problem
from nr.auth.rbac import EMERGENCY_ROLES, ROLES
from nr.auth.sessions import CurrentUser
from nr.crypto import encrypt
from nr.db import get_session
from nr.feed import health

router = APIRouter(prefix="/api", tags=["admin"])
ADMIN = require_roles("admin")
CHIEF = require_roles("admin", "editor_in_chief")
EDITABLE_SETTINGS = ("detect", "gate", "budgets", "ai", "guard", "review", "columns", "styles", "desk", "expiry")


@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    t = now()
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    vis = visible_accounts(s, user)
    acc_filter = "" if vis is None else "WHERE acc.id = ANY(:vis)"
    return {
        "as_of": t,
        "source": health.overview(s, t),
        "emergency_stop": settings_store.get(s, "emergency_stop"),
        "ai": {**settings_store.get(s, "ai"),
               "models": rows(s, "SELECT id, name, model, enabled, last_test_at, last_test_ok FROM ai_model_config"),
               "cost_today": float(s.execute(text("SELECT coalesce(sum(cost_usd), 0) FROM ai_call_log WHERE created_at >= :d"),
                                             {"d": day}).scalar()),
               "budget_today": settings_store.get(s, "budgets")["global_daily_usd"],
               "queued": s.execute(text("""SELECT count(*) FROM job WHERE status = 'queued'
                                           AND kind IN ('eic_evaluate', 'draft_generate')""")).scalar(),
               "failed_24h": s.execute(text("""SELECT count(*) FROM job WHERE status = 'dead'
                                               AND finished_at > :d"""), {"d": t - timedelta(hours=24)}).scalar()},
        "states": dict(s.execute(text("""SELECT state, count(*) FROM news_event WHERE detected_at > :d GROUP BY state"""),
                                 {"d": t - timedelta(days=7)}).all()),
        "top_candidates": rows(s, """SELECT e.id, e.event_class, e.coin, e.venue, e.direction, e.score, e.priority,
                                            e.state, e.detected_at, t.trader_key, t.display_name
                                     FROM news_event e JOIN trader t ON t.id = e.trader_id
                                     WHERE e.state IN ('candidate', 'evaluated') AND e.gate_status = 'pass'
                                     ORDER BY e.priority NULLS LAST, e.score DESC LIMIT 12"""),
        "pending_review": rows(s, f"""SELECT a.id, a.status, acc.handle, a.language, v.headline, a.updated_at
                                     FROM article a JOIN editorial_account acc ON acc.id = a.account_id
                                     LEFT JOIN article_version v ON v.id = a.current_version_id
                                     WHERE a.status IN ('in_review', 'approved', 'stale')
                                     {"" if vis is None else "AND acc.id = ANY(:vis)"}
                                     ORDER BY a.updated_at DESC LIMIT 20""", {"vis": vis}),
        # today's publishing at a glance: what is waiting, what went out, what ran out of time, what the AI reviewer
        # sent back to a person
        "publishing": one(s, f"""
            SELECT count(*) FILTER (WHERE a.status = 'approved' AND a.deleted_at IS NULL) AS ready,
                   count(*) FILTER (WHERE a.status = 'approved' AND a.deleted_at IS NULL
                                    AND a.expires_at < :soon) AS expiring_soon,
                   count(*) FILTER (WHERE a.status IN ('draft_ready', 'changes_requested', 'in_review', 'stale')
                                    AND a.deleted_at IS NULL) AS todo,
                   count(*) FILTER (WHERE a.status IN ('draft_ready', 'changes_requested', 'in_review')
                                    AND a.deleted_at IS NULL AND (SELECT r.verdict FROM article_review r
                                      WHERE r.article_version_id = a.current_version_id ORDER BY r.id DESC LIMIT 1)
                                    = 'reject') AS reviewer_rejected,
                   count(*) FILTER (WHERE a.status = 'expired' AND a.expires_at >= :d) AS expired_today,
                   (SELECT count(*) FROM publication_record pr WHERE pr.status = 'published' AND pr.published_at >= :d
                      {"" if vis is None else "AND pr.account_id = ANY(:vis)"}) AS published_today
            FROM article a {"" if vis is None else "WHERE a.account_id = ANY(:vis)"}""",
            {"d": day, "soon": t + timedelta(minutes=30), "vis": vis}),
        "accounts": rows(s, f"""SELECT acc.id, acc.handle, acc.display_name, acc.language, acc.status,
                                       acc.authorization_status, acc.authorization_expires_at, acc.daily_post_budget,
                                       (SELECT count(*) FROM publication_record pr WHERE pr.account_id = acc.id
                                        AND pr.published_at >= :d) AS published_today,
                                       (SELECT count(*) FROM article a WHERE a.account_id = acc.id
                                        AND a.status = 'approved' AND a.deleted_at IS NULL) AS ready
                                FROM editorial_account acc {acc_filter} ORDER BY acc.id""", {"d": day, "vis": vis}),
        "open_corrections": s.execute(text("SELECT count(*) FROM correction_record WHERE status = 'open'")).scalar(),
        "expiring_authorizations": rows(s, """SELECT id, handle, authorization_expires_at FROM editorial_account
                                              WHERE authorization_status = 'authorized'
                                                AND authorization_expires_at < :t ORDER BY authorization_expires_at""",
                                        {"t": t + timedelta(days=7)}),
    }


class AccountIn(BaseModel):
    handle: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,40}$")  # X handles are up to 15; OKX Square names longer
    display_name: str = Field(max_length=100)
    platform: Literal["x", "okx_square"] = "x"
    venues: list[Literal["hyperliquid", "okx"]] = Field(default_factory=lambda: ["hyperliquid"])
    language: Literal["zh", "en"]
    status: Literal["active", "paused", "retired"] = "paused"
    brand: str | None = Field(None, max_length=64)
    positioning: str = Field("", max_length=2000)
    audience_profile: dict = Field(default_factory=dict)
    timezone: str = "UTC"
    style_key: str | None = None
    allowed_event_classes: list[str] = Field(default_factory=list)
    min_score: float = 0
    daily_post_budget: int = Field(5, ge=0, le=100)
    min_gap_minutes: int = Field(60, ge=0)
    long_post_allowed: bool = False
    link_in_thread: bool = True  # the full address goes in the post's first reply
    reply_explorer_link: bool = False  # ...with an explorer link (off: no free ads for the explorer)
    rpa_enabled: bool = False
    authorization_status: Literal["pending", "authorized", "expired", "revoked"] = "pending"
    authorization_expires_at: str | None = None
    authorization_note: str | None = Field(None, max_length=1000)


@router.get("/accounts")
def list_accounts(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    vis = visible_accounts(s, user)
    return rows(s, f"""SELECT * FROM editorial_account {"" if vis is None else "WHERE id = ANY(:vis)"}
                       ORDER BY id""", {"vis": vis})


def _account_params(body: AccountIn) -> dict:
    d = body.model_dump()
    d["audience_profile"] = json.dumps(d["audience_profile"], ensure_ascii=False)
    return d


@router.post("/accounts")
def create_account(body: AccountIn, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    d = _account_params(body)
    aid = s.execute(text(f"""INSERT INTO editorial_account ({", ".join(d)}) VALUES
                             ({", ".join(("CAST(:audience_profile AS jsonb)" if k == "audience_profile" else ":" + k)
                                         for k in d)}) RETURNING id"""), d).scalar_one()
    audit.record(s, "account.create", "account", aid, user_id=user.id, account_id=aid, after=body.model_dump())
    s.commit()
    return {"id": aid}


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountIn, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(CHIEF)):
    before = one(s, "SELECT * FROM editorial_account WHERE id = :a", {"a": account_id})
    if before is None:
        raise HTTPException(404, "account not found")
    d = _account_params(body)
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "audience_profile" else f"{k} = :{k}" for k in d)
    s.execute(text(f"UPDATE editorial_account SET {sets}, updated_at = now() WHERE id = :id"), {**d, "id": account_id})
    audit.record(s, "account.update", "account", account_id, user_id=user.id, account_id=account_id, before=before,
                 after=body.model_dump())
    s.commit()
    return {"ok": True}


class PermissionsIn(BaseModel):
    user_id: int
    permissions: list[Literal["view", "draft", "review", "publish", "manage"]]


@router.put("/accounts/{account_id}/permissions")
def set_permissions(account_id: int, body: PermissionsIn, s: Session = Depends(get_session),
                    user: CurrentUser = Depends(ADMIN)):
    s.execute(text("DELETE FROM account_permission WHERE user_id = :u AND account_id = :a"),
              {"u": body.user_id, "a": account_id})
    for p in dict.fromkeys(body.permissions):
        s.execute(text("INSERT INTO account_permission VALUES (:u, :a, :p)"), {"u": body.user_id, "a": account_id, "p": p})
    audit.record(s, "account.permissions", "account", account_id, user_id=user.id, account_id=account_id,
                 after=body.model_dump())
    s.commit()
    return {"ok": True}


@router.get("/accounts/{account_id}/permissions")
def get_permissions(account_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    return rows(s, """SELECT u.id AS user_id, u.username, array_agg(p.permission ORDER BY p.permission) AS permissions
                      FROM account_permission p JOIN app_user u ON u.id = p.user_id WHERE p.account_id = :a
                      GROUP BY u.id, u.username ORDER BY u.username""", {"a": account_id})


@router.get("/styles")
def list_styles(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return rows(s, "SELECT * FROM editorial_style ORDER BY key, version DESC")


@router.get("/settings")
def get_settings_all(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return {k: settings_store.get(s, k) for k in (*EDITABLE_SETTINGS, "emergency_stop")}


@router.put("/settings/{key}")
def put_setting(key: str, value: dict, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    if key not in EDITABLE_SETTINGS:
        raise HTTPException(404, "unknown setting")
    unknown = set(value) - set(settings_store.DEFAULTS[key])
    if unknown:
        raise HTTPException(422, f"unknown fields {sorted(unknown)}")
    before = settings_store.get(s, key)
    after = settings_store.put(s, key, value, user.id)
    audit.record(s, "settings.update", "setting", key, user_id=user.id, before=before, after=after)
    s.commit()
    return after


class EmergencyIn(BaseModel):
    active: bool
    reason: str = Field("", max_length=1000)


@router.post("/settings/emergency-stop")
def emergency_stop(body: EmergencyIn, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(require_roles(*EMERGENCY_ROLES))):
    """Stops all publishing and AI work at once; data sync and detection keep running."""
    v = settings_store.put(s, "emergency_stop", {"active": body.active, "reason": body.reason,
                                                 "since": now().isoformat() if body.active else None}, user.id)
    if body.active:
        s.execute(text("UPDATE publication_plan SET status = 'cancelled' WHERE status = 'planned'"))
    audit.record(s, "emergency_stop." + ("on" if body.active else "off"), "setting", "emergency_stop", user_id=user.id,
                 reason=body.reason)
    s.commit()
    return v


@router.get("/sources")
def sources(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return {"overview": health.overview(s, now()),
            "connections": rows(s, "SELECT * FROM source_connection ORDER BY id"),
            "runs": rows(s, "SELECT * FROM sync_run ORDER BY started_at DESC LIMIT 30"),
            "health": rows(s, "SELECT at, reachable, status FROM source_health_snapshot ORDER BY at DESC LIMIT 30")}


class ModelIn(BaseModel):
    name: str = Field(max_length=64)
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages"]
    base_url: str = Field(pattern=r"^https?://", max_length=300)
    model: str = Field(max_length=128)
    api_key: str | None = Field(None, max_length=500)
    auth_header: Literal["bearer", "x-api-key"] = "bearer"
    anthropic_version: str = "2023-06-01"
    json_mode: Literal["none", "json_object", "json_schema"] = "json_object"
    timeout_s: int = Field(90, ge=5, le=600)
    max_retries: int = Field(2, ge=0, le=5)
    max_concurrency: int = Field(2, ge=1, le=8)
    input_usd_per_mtok: float = Field(0, ge=0)
    output_usd_per_mtok: float = Field(0, ge=0)
    extra_body: dict = Field(default_factory=dict)
    enabled: bool = True


MODEL_COLS = """id, name, protocol, base_url, model, auth_header, anthropic_version, json_mode, extra_body, timeout_s, max_retries,
    max_concurrency, input_usd_per_mtok, output_usd_per_mtok, enabled, last_test_at, last_test_ok, last_test_detail,
    api_key_secret IS NOT NULL AS has_key"""


@router.get("/ai/model-options")
def model_options(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """Names only, for the desk's model pickers: no URLs, no keys."""
    return rows(s, "SELECT id, name, model, last_test_ok FROM ai_model_config WHERE enabled ORDER BY id")


@router.get("/ai/models")
def list_models(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    """Each model with what its last failed test means, worded by the web app (`diagnosis`)."""
    out = rows(s, f"SELECT {MODEL_COLS} FROM ai_model_config ORDER BY id")
    for m in out:
        d = m["last_test_detail"] or {}
        m["diagnosis"] = None if m["last_test_ok"] is not False else \
            d.get("diagnosis") or diagnose(d.get("status"), str(d.get("detail") or ""))
    return out


@router.post("/ai/models")
def create_model(body: ModelIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    d = body.model_dump()
    key = d.pop("api_key")
    d["api_key_secret"] = encrypt(key) if key else None
    d["extra_body"] = json.dumps(d["extra_body"])
    cols = ", ".join("CAST(:extra_body AS jsonb)" if k == "extra_body" else ":" + k for k in d)
    mid = s.execute(text(f"INSERT INTO ai_model_config ({', '.join(d)}) VALUES ({cols}) RETURNING id"), d).scalar_one()
    audit.record(s, "ai.model_create", "ai_model", mid, user_id=user.id, after={k: v for k, v in d.items()
                                                                                if k != "api_key_secret"})
    s.commit()
    return {"id": mid}


@router.put("/ai/models/{model_id}")
def update_model(model_id: int, body: ModelIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    d = body.model_dump()
    key = d.pop("api_key")
    d["extra_body"] = json.dumps(d["extra_body"])
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "extra_body" else f"{k} = :{k}" for k in d)
    if key:  # an empty key keeps the stored one
        sets += ", api_key_secret = :secret"
    n = s.execute(text(f"UPDATE ai_model_config SET {sets}, updated_at = now() WHERE id = :id"),
                  {**d, "secret": encrypt(key) if key else None, "id": model_id}).rowcount
    if not n:
        raise HTTPException(404, "model not found")
    audit.record(s, "ai.model_update", "ai_model", model_id, user_id=user.id, after={**d, "key_changed": bool(key)})
    s.commit()
    return {"ok": True}


@router.post("/ai/models/{model_id}/test")
def run_model_test(model_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    out = test_model(s, model_id, now())
    audit.record(s, "ai.model_test", "ai_model", model_id, user_id=user.id, after={"ok": out["ok"]})
    s.commit()
    return out


@router.get("/ai/agents")
def list_agents(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    return rows(s, """SELECT a.*, m.name AS model_name, f.name AS fallback_name, f.last_test_ok AS fallback_test_ok,
                             m.last_test_ok AS model_test_ok FROM ai_agent_profile a
                      LEFT JOIN ai_model_config m ON m.id = a.model_config_id
                      LEFT JOIN ai_model_config f ON f.id = a.fallback_model_config_id
                      ORDER BY a.agent_key, a.version DESC""")


class AgentIn(BaseModel):
    model_config_id: int | None = None
    fallback_model_config_id: int | None = None
    prompt_version: int | None = None
    max_output_tokens: int = Field(2500, ge=200, le=16000)
    budget_usd_per_task: float = Field(0.3, ge=0)
    display_name: str | None = None
    role_description: str | None = None


def prompt_versions(s: Session, key: str) -> list[int]:
    return list(s.execute(text("SELECT DISTINCT version FROM prompt_template WHERE key = :k ORDER BY version"),
                          {"k": key}).scalars())


def _agent_version(s: Session, cur, user_id: int, **changes) -> int:
    """Agent profiles are versioned: a change creates a new active version, the old one stays for the record."""
    d = {k: cur[k] for k in ("display_name", "role_description", "model_config_id", "fallback_model_config_id",
                             "prompt_version", "max_output_tokens", "budget_usd_per_task")} | changes
    have = prompt_versions(s, cur["prompt_key"])
    if d["prompt_version"] not in have:
        raise HTTPException(422, f"{cur['agent_key']} has no prompt version {d['prompt_version']}; "
                                 f"available: {', '.join(f'v{v}' for v in have)}")
    s.execute(text("UPDATE ai_agent_profile SET active = false WHERE id = :i"), {"i": cur["id"]})
    return s.execute(text("""
        INSERT INTO ai_agent_profile (agent_key, version, display_name, role_description, model_config_id,
          fallback_model_config_id, prompt_key, prompt_version, max_output_tokens, budget_usd_per_task, created_by)
        VALUES (:k, :v, :display_name, :role_description, :model_config_id, :fallback_model_config_id, :pk,
          :prompt_version, :max_output_tokens, :budget_usd_per_task, :u) RETURNING id"""),
        d | {"k": cur["agent_key"], "v": cur["version"] + 1, "pk": cur["prompt_key"], "u": user_id}).scalar_one()


@router.put("/ai/agents/{agent_key}")
def new_agent_version(agent_key: str, body: AgentIn, s: Session = Depends(get_session),
                      user: CurrentUser = Depends(ADMIN)):
    cur = one(s, "SELECT * FROM ai_agent_profile WHERE agent_key = :k AND active", {"k": agent_key})
    if cur is None:
        raise HTTPException(404, "agent not found")
    # only what the request sends changes; an empty model field means "no model", an empty text keeps the old one
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    for k in ("prompt_version", "display_name", "role_description"):
        if k in sent and not sent[k]:
            del sent[k]
    nid = _agent_version(s, cur, user.id, **sent)
    audit.record(s, "ai.agent_version", "ai_agent", agent_key, user_id=user.id, before={"version": cur["version"]},
                 after=body.model_dump())
    s.commit()
    return {"id": nid, "version": cur["version"] + 1}


class PromptSwitchIn(BaseModel):
    prompt_version: int | None = Field(None, ge=1)  # None: each role's newest version
    agent_keys: list[str] = Field(min_length=1, max_length=20)


@router.post("/ai/agents/prompt-version")
def switch_prompt_version(body: PromptSwitchIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    """Point several roles at one prompt version; a role without that version is skipped and says why. Nothing
    else about the role changes."""
    changed, skipped = [], []
    for key in dict.fromkeys(body.agent_keys):
        cur = one(s, "SELECT * FROM ai_agent_profile WHERE agent_key = :k AND active", {"k": key})
        have = prompt_versions(s, cur["prompt_key"]) if cur else []
        want = body.prompt_version or (have[-1] if have else None)
        if cur is None:
            skipped.append({"agent": key, "reason": "unknown role"})
        elif cur["prompt_version"] == want:
            skipped.append({"agent": key, "reason": "already on this version"})
        elif want not in have:
            skipped.append({"agent": key, "reason": f"no v{want}; available: {', '.join(f'v{v}' for v in have)}"})
        else:
            _agent_version(s, cur, user.id, prompt_version=want)
            changed.append(key)
    audit.record(s, "ai.agent_prompt_switch", "ai_agent", f"{len(changed)} roles", user_id=user.id,
                 after={**body.model_dump(), "changed": changed})  # the roles are listed in `after`
    s.commit()
    return {"changed": changed, "skipped": skipped}


class BindIn(BaseModel):
    model_config_id: int
    fallback_model_config_id: int | None = None
    only_unbound: bool = True


@router.post("/ai/agents/bind")
def bind_agents(body: BindIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    """One click for the common setup: every role (or every role without a model yet) gets this model."""
    for mid in filter(None, (body.model_config_id, body.fallback_model_config_id)):
        if not s.execute(text("SELECT 1 FROM ai_model_config WHERE id = :i AND enabled"), {"i": mid}).scalar():
            raise HTTPException(422, f"model {mid} not found or disabled")
    changed = []
    for cur in s.execute(text("SELECT * FROM ai_agent_profile WHERE active ORDER BY agent_key")).mappings().all():
        if body.only_unbound and cur["model_config_id"] is not None:
            continue
        _agent_version(s, cur, user.id, model_config_id=body.model_config_id,
                       fallback_model_config_id=body.fallback_model_config_id)
        changed.append(cur["agent_key"])
    audit.record(s, "ai.agent_bind", "ai_agent", None, user_id=user.id, after=body.model_dump() | {"agents": changed})
    s.commit()
    return {"changed": changed}


def ai_readiness(s: Session) -> dict:
    """Why AI work is (not) running, in one place: no URLs or keys, so every signed-in user may see it."""
    models = rows(s, "SELECT id, name, model, last_test_ok FROM ai_model_config WHERE enabled ORDER BY id")
    usable = {m["id"] for m in models}
    agents = rows(s, "SELECT agent_key, model_config_id FROM ai_agent_profile WHERE active ORDER BY agent_key")
    bound = {a["agent_key"]: a["model_config_id"] in usable for a in agents}
    # reporter_* roles fall back to the base reporter's models; eic and reporter must have their own
    unbound = [k for k, ok in bound.items() if not ok and not (k.startswith("reporter_") and bound.get("reporter"))]
    waiting = rows(s, """SELECT kind, count(*) AS n, max(last_error) AS last_error FROM job
                         WHERE status = 'queued' AND kind IN ('eic_evaluate', 'draft_generate') GROUP BY kind""")
    return {"enabled": settings_store.get(s, "ai")["enabled"], "models": models,
            "tested_ok": [m["id"] for m in models if m["last_test_ok"]],
            "core_unbound": [k for k in ("eic", "reporter") if k in unbound], "unbound": unbound, "waiting": waiting}


@router.get("/ai/status")
def ai_status(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return ai_readiness(s)


BLOCKING = ("firewall", "bad_key", "no_channel", "quota")  # causes a retry never gets past


def ai_fixes(s: Session, t) -> list[dict]:
    """What to change so the failures stop, each with the one action that does it: roles on an old prompt, and
    models that can't be reached (a failed test, or blocked calls in the last day) with a working model to use instead."""
    fixes: list[dict] = []
    behind = list(s.execute(text("""
        SELECT a.agent_key FROM ai_agent_profile a WHERE a.active
          AND a.prompt_version < (SELECT max(version) FROM prompt_template p WHERE p.key = a.prompt_key)
        ORDER BY a.agent_key""")).scalars())
    if behind:
        fixes.append({"kind": "prompts_behind", "agents": behind})
    models = rows(s, "SELECT id, name, model, last_test_ok FROM ai_model_config WHERE enabled ORDER BY id")
    blocked: dict[int, list[str]] = {}
    for r in rows(s, """SELECT model_config_id, status, error FROM ai_call_log
                        WHERE status <> 'ok' AND model_config_id IS NOT NULL AND created_at > :d""", {"d": t - timedelta(hours=24)}):
        if (code := diagnose(r["status"], r["error"])) in BLOCKING:
            blocked.setdefault(r["model_config_id"], []).append(code)
    bad = {m["id"]: ("test_failed" if m["last_test_ok"] is False else max(set(blocked[m["id"]]), key=blocked[m["id"]].count))
           for m in models if m["last_test_ok"] is False or len(blocked.get(m["id"], [])) >= 2}
    agents = rows(s, "SELECT agent_key, model_config_id, fallback_model_config_id FROM ai_agent_profile WHERE active")
    use = {}
    for a in agents:
        if a["model_config_id"] is not None:
            use[a["model_config_id"]] = use.get(a["model_config_id"], 0) + 1
    good = sorted((m for m in models if m["last_test_ok"] and m["id"] not in bad), key=lambda m: -use.get(m["id"], 0))
    for m in models:
        if m["id"] not in bad:
            continue
        primary = sorted(a["agent_key"] for a in agents if a["model_config_id"] == m["id"])
        fallback = sorted(a["agent_key"] for a in agents if a["fallback_model_config_id"] == m["id"])
        if primary or fallback:
            fixes.append({"kind": "replace_model", "model": {"id": m["id"], "name": m["name"]}, "reason": bad[m["id"]],
                          "primary_in": primary, "fallback_in": fallback,
                          "suggest": {"id": good[0]["id"], "name": good[0]["name"]} if good else None})
    return fixes


@router.get("/ai/fixes")
def list_fixes(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return {"fixes": ai_fixes(s, now())}


class ReplaceModelIn(BaseModel):
    from_model_id: int
    to_model_id: int | None = None  # None: only drop it where it is a fallback


@router.post("/ai/agents/replace-model")
def replace_model(body: ReplaceModelIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    """Every role using a model (as its model or its fallback) switches to another; a fallback that would equal the
    role's own model is dropped instead."""
    if body.to_model_id is not None and not s.execute(
            text("SELECT 1 FROM ai_model_config WHERE id = :i AND enabled"), {"i": body.to_model_id}).scalar():
        raise HTTPException(422, f"model {body.to_model_id} not found or disabled")
    changed, skipped = [], []
    for cur in s.execute(text("SELECT * FROM ai_agent_profile WHERE active ORDER BY agent_key")).mappings().all():
        primary, fallback = cur["model_config_id"], cur["fallback_model_config_id"]
        if body.from_model_id not in (primary, fallback):
            continue
        if primary == body.from_model_id:
            if body.to_model_id is None:
                skipped.append({"agent": cur["agent_key"], "reason": "it is this role's own model: pick a replacement"})
                continue
            primary = body.to_model_id
        if fallback == body.from_model_id:
            fallback = body.to_model_id
        if fallback == primary:
            fallback = None
        _agent_version(s, cur, user.id, model_config_id=primary, fallback_model_config_id=fallback)
        changed.append(cur["agent_key"])
    audit.record(s, "ai.agent_replace_model", "ai_agent", f"{len(changed)} roles", user_id=user.id,
                 after={**body.model_dump(), "changed": changed})
    s.commit()
    return {"changed": changed, "skipped": skipped}


@router.get("/onboarding")
def onboarding(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """The getting-started checklist, each step done or not, computed from the data rather than remembered."""
    ready = ai_readiness(s)
    one_of = lambda sql: bool(s.execute(text(sql)).scalar())  # noqa: E731
    behind = s.execute(text("""
        SELECT count(*) FROM ai_agent_profile a WHERE a.active AND a.agent_key LIKE 'reporter%'
          AND a.prompt_version < (SELECT max(version) FROM prompt_template p WHERE p.key = a.prompt_key)""")).scalar()
    steps = [
        ("model", bool(ready["tested_ok"]), "ai"),
        ("bind", bool(ready["models"]) and not ready["core_unbound"], "agents"),
        ("account", one_of("SELECT 1 FROM editorial_account WHERE status = 'active' LIMIT 1"), None),
        ("prompts", behind == 0, "agents"),
        ("trial", one_of("SELECT 1 FROM article_version WHERE origin = 'ai' LIMIT 1"), None),
        ("publish", one_of("SELECT 1 FROM publication_record LIMIT 1"), None),
    ]
    return {"steps": [{"key": k, "done": done, "tab": tab} for k, done, tab in steps],
            "done": all(d for _, d, _ in steps)}


@router.get("/ai/prompts")
def list_prompts(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    """Every version with who uses it now and how often it was called; only a never-used, non-seed version can go."""
    return rows(s, """
        SELECT p.*, coalesce(c.calls, 0) AS calls,
               (SELECT array_agg(a.agent_key) FROM ai_agent_profile a
                WHERE a.active AND a.prompt_key = p.key AND a.prompt_version = p.version) AS active_for,
               coalesce(p.notes, '') <> 'seed' AND coalesce(c.calls, 0) = 0
                 AND NOT EXISTS (SELECT 1 FROM ai_agent_profile a WHERE a.prompt_key = p.key AND a.prompt_version = p.version)
                 AND NOT EXISTS (SELECT 1 FROM article_version v
                                 WHERE v.prompt_ref = p.key || '@v' || p.version || coalesce('.' || p.language, ''))
                 AS deletable
        FROM prompt_template p
        LEFT JOIN (SELECT prompt_ref, count(*) AS calls FROM ai_call_log GROUP BY 1) c
          ON c.prompt_ref = p.key || '@v' || p.version || coalesce('.' || p.language, '')
        ORDER BY p.key, p.version DESC, p.language NULLS FIRST""")


@router.delete("/ai/prompts/{key}/{version}")
def delete_prompt_version(key: str, version: int, language: Literal["zh", "en"] | None = None,
                          s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    p = next((r for r in list_prompts(s, user) if r["key"] == key and r["version"] == version
              and r["language"] == language), None)
    if p is None:
        raise HTTPException(404, "prompt version not found")
    if not p["deletable"]:
        raise HTTPException(409, "a seed version, or one a role or a draft has used, is kept so drafts stay traceable")
    s.execute(text("""DELETE FROM prompt_template WHERE key = :k AND version = :v
                      AND coalesce(language, '') = coalesce(:l, '')"""), {"k": key, "v": version, "l": language})
    audit.record(s, "ai.prompt_delete", "prompt", f"{key}@v{version}" + (f".{language}" if language else ""),
                 user_id=user.id)
    s.commit()
    return {"ok": True}


class PromptIn(BaseModel):
    key: str = Field(max_length=48)
    language: Literal["zh", "en"] | None = None
    # add this language's text to an existing version instead of starting a new one
    version: int | None = Field(None, ge=1)
    system_text: str = Field(min_length=20, max_length=20000)
    user_template: str = Field(min_length=5, max_length=5000)
    notes: str | None = Field(None, max_length=1000)


@router.post("/ai/prompts")
def new_prompt_version(body: PromptIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    if not s.execute(text("SELECT 1 FROM ai_agent_profile WHERE prompt_key = :k"), {"k": body.key}).scalar():
        raise HTTPException(422, f"no role uses the prompt key {body.key}")
    if body.version is not None:  # another language's text for a version no role has run yet
        if body.version not in prompt_versions(s, body.key):
            raise HTTPException(404, f"{body.key} has no version {body.version}")
        if s.execute(text("SELECT 1 FROM ai_agent_profile WHERE prompt_key = :k AND prompt_version = :v"),
                     {"k": body.key, "v": body.version}).scalar():
            raise HTTPException(409, "a role has used this version: save a new version instead")
        if s.execute(text("""SELECT 1 FROM prompt_template WHERE key = :k AND version = :v
                             AND coalesce(language, '') = coalesce(:l, '')"""),
                     {"k": body.key, "v": body.version, "l": body.language}).scalar():
            raise HTTPException(409, "this version already has that language: save a new version instead")
        v = body.version
    else:
        v = s.execute(text("SELECT coalesce(max(version), 0) + 1 FROM prompt_template WHERE key = :k"),
                      {"k": body.key}).scalar()
    s.execute(text("""INSERT INTO prompt_template (key, version, language, system_text, user_template, sha256, notes,
                                                   created_by)
                      VALUES (:k, :v, :l, :s, :u, :h, :n, :by)"""),
              {"k": body.key, "v": v, "l": body.language, "s": body.system_text, "u": body.user_template,
               "n": body.notes, "by": user.id,
               "h": hashlib.sha256((body.system_text + body.user_template).encode()).hexdigest()})
    ref = f"{body.key}@v{v}" + (f".{body.language}" if body.language else "")
    audit.record(s, "ai.prompt_version", "prompt", ref, user_id=user.id)
    s.commit()
    return {"key": body.key, "version": v, "language": body.language}


@router.get("/ai/costs")
def ai_costs(s: Session = Depends(get_session), user: CurrentUser = Depends(require_roles("admin", "editor_in_chief",
                                                                                            "analyst")),
             days: int = Query(14, ge=1, le=90)):
    since = now() - timedelta(days=days)
    return {"by_day": rows(s, """SELECT date_trunc('day', created_at) AS day, agent_key, count(*) AS calls,
                                        sum(cost_usd) AS cost, sum(input_tokens) AS input_tokens,
                                        sum(output_tokens) AS output_tokens
                                 FROM ai_call_log WHERE created_at > :s GROUP BY 1, 2 ORDER BY 1 DESC, 2""", {"s": since}),
            "by_status": rows(s, """SELECT status, count(*) AS n FROM ai_call_log WHERE created_at > :s GROUP BY 1
                                    ORDER BY 2 DESC""", {"s": since}),
            "recent": rows(s, """SELECT id, created_at, agent_key, purpose, model, status, cost_usd, latency_ms, error,
                                        news_event_id, article_id FROM ai_call_log ORDER BY id DESC LIMIT 50"""),
            "budgets": settings_store.get(s, "budgets")}


class UserIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{2,64}$")
    display_name: str | None = Field(None, max_length=128)
    password: str = Field(max_length=256)
    roles: list[str]


@router.get("/users")
def list_users(s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    return rows(s, """SELECT u.id, u.username, u.display_name, u.is_active, u.must_change_password, u.last_login_at,
                             u.locked_until, coalesce(array_agg(r.role) FILTER (WHERE r.role IS NOT NULL), '{}') AS roles
                      FROM app_user u LEFT JOIN user_role r ON r.user_id = u.id GROUP BY u.id ORDER BY u.id""")


def _check_roles(roles: list[str]) -> None:
    bad = set(roles) - set(ROLES)
    if bad or not roles:
        raise HTTPException(422, f"roles must be among {ROLES}")


@router.post("/users")
def create_user(body: UserIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    _check_roles(body.roles)
    if problem := password_problem(body.password):
        raise HTTPException(422, problem)
    uid = s.execute(text("""INSERT INTO app_user (username, display_name, password_hash, must_change_password)
                            VALUES (:u, :d, :p, true) RETURNING id"""),
                    {"u": body.username, "d": body.display_name, "p": hash_password(body.password)}).scalar_one()
    for r in body.roles:
        s.execute(text("INSERT INTO user_role VALUES (:u, :r)"), {"u": uid, "r": r})
    audit.record(s, "user.create", "user", uid, user_id=user.id, after={"username": body.username, "roles": body.roles})
    s.commit()
    return {"id": uid}


class UserUpdate(BaseModel):
    roles: list[str] | None = None
    is_active: bool | None = None
    reset_password: str | None = Field(None, max_length=256)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserUpdate, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    if body.roles is not None:
        _check_roles(body.roles)
        if uid == user.id and "admin" not in body.roles:
            raise HTTPException(409, "you cannot remove your own admin role")
        s.execute(text("DELETE FROM user_role WHERE user_id = :u"), {"u": uid})
        for r in body.roles:
            s.execute(text("INSERT INTO user_role VALUES (:u, :r)"), {"u": uid, "r": r})
    if body.is_active is not None:
        if uid == user.id and not body.is_active:
            raise HTTPException(409, "you cannot deactivate yourself")
        s.execute(text("UPDATE app_user SET is_active = :a WHERE id = :u"), {"a": body.is_active, "u": uid})
        if not body.is_active:
            s.execute(text("UPDATE user_session SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"),
                      {"u": uid})
    if body.reset_password:
        if problem := password_problem(body.reset_password):
            raise HTTPException(422, problem)
        s.execute(text("""UPDATE app_user SET password_hash = :p, must_change_password = true, failed_logins = 0,
                                 locked_until = NULL WHERE id = :u"""), {"p": hash_password(body.reset_password), "u": uid})
    audit.record(s, "user.update", "user", uid, user_id=user.id,
                 after={"roles": body.roles, "is_active": body.is_active, "password_reset": bool(body.reset_password)})
    s.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF),
              object_type: str | None = None, action: str | None = None, limit: int = Query(200, le=1000)):
    return rows(s, f"""SELECT l.*, u.username FROM audit_log l LEFT JOIN app_user u ON u.id = l.actor_user_id
                       WHERE true {"AND l.object_type = :t" if object_type else ""}
                             {"AND l.action LIKE :a" if action else ""}
                       ORDER BY l.id DESC LIMIT :n""", {"t": object_type, "a": f"{action}%", "n": limit})


@router.get("/jobs")
def jobs(s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF),
         status: Literal["queued", "running", "done", "dead", "cancelled"] | None = None):
    return rows(s, f"""SELECT id, kind, payload, status, priority, run_after, attempts, max_attempts, last_error,
                              created_at, finished_at FROM job {"WHERE status = :st" if status else ""}
                       ORDER BY id DESC LIMIT 200""", {"st": status})


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    n = s.execute(text("""UPDATE job SET status = 'queued', attempts = 0, run_after = now(), last_error = NULL
                          WHERE id = :i AND status IN ('dead', 'cancelled')"""), {"i": job_id}).rowcount
    if not n:
        raise HTTPException(409, "only dead or cancelled jobs can be retried")
    s.commit()
    return {"ok": True}

