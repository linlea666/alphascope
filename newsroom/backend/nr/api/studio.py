"""Phase 2-3 desk tools: columns, model comparison and AI revisions, the assignment desk, the publication calendar,
the RPA hand-off and the style library."""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.api.common import now, one, rows
from nr.auth.deps import active_user, check_account, require_roles, visible_accounts
from nr.auth.rbac import EVENT_DESK_ROLES
from nr.auth.sessions import CurrentUser
from nr.columns.defs import COLUMNS
from nr.db import get_session
from nr.jobs.queue import enqueue
from nr.publish import service as pub

router = APIRouter(prefix="/api", tags=["studio"])
CHIEF = require_roles("admin", "editor_in_chief")


# ---- columns ---------------------------------------------------------------------------------------------------

@router.get("/columns")
def list_columns(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    disabled = set(settings_store.get(s, "columns")["disabled"])
    latest = {r["column_key"]: r for r in rows(s, """
        SELECT DISTINCT ON (column_key) column_key, id, period_key, state, gate_status, detected_at, blocking_issues
        FROM news_event WHERE column_key IS NOT NULL ORDER BY column_key, detected_at DESC""")}
    counts = dict(s.execute(text("SELECT column_key, count(*) FROM news_event WHERE column_key IS NOT NULL "
                                 "GROUP BY 1")).all())
    return [{"key": c.key, "zh": c.zh, "en": c.en, "cadence": c.cadence, "phase": c.phase, "venue": c.venue,
             "enabled": c.key not in disabled, "issues": counts.get(c.key, 0), "latest": latest.get(c.key)}
            for c in COLUMNS.values()]


class ColumnRun(BaseModel):
    params: dict = Field(default_factory=dict)


@router.post("/columns/{key}/run")
def run_column(key: str, body: ColumnRun, s: Session = Depends(get_session),
               user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    if key not in COLUMNS:
        raise HTTPException(404, "unknown column")
    if key == "dossier" and not body.params.get("trader_key"):
        raise HTTPException(422, "dossier needs params.trader_key")
    if key == "recap" and not body.params.get("episode_id"):
        raise HTTPException(422, "recap needs params.episode_id")
    tag = hashlib.sha256(json.dumps(body.params, sort_keys=True).encode()).hexdigest()[:12]
    jid = enqueue(s, "column_build", {"column": key, "params": body.params}, dedupe_key=f"column:{key}:manual:{tag}",
                  priority=40)
    audit.record(s, "column.run", "column", key, user_id=user.id, after=body.params)
    s.commit()
    return {"job_id": jid}


# ---- AI drafting modes and model comparison ---------------------------------------------------------------------

def _assignment_of_article(s: Session, article_id: int) -> dict:
    a = one(s, """SELECT ar.account_id, ar.assignment_id, asg.status FROM article ar
                  LEFT JOIN assignment asg ON asg.id = ar.assignment_id WHERE ar.id = :a""", {"a": article_id})
    if a is None or a["assignment_id"] is None:
        raise HTTPException(404, "article not found or not assigned")
    return a


class DraftRequest(BaseModel):
    mode: Literal["draft", "polish", "fix"] = "draft"
    model_config_id: int | None = None


@router.post("/articles/{article_id}/ai")
def ai_revision(article_id: int, body: DraftRequest, s: Session = Depends(get_session),
                user: CurrentUser = Depends(active_user)):
    """Ask the assigned reporter for a draft, the desk editor for a polish, or the fact checker for a fix."""
    a = _assignment_of_article(s, article_id)
    check_account(s, user, a["account_id"], "draft")
    jid = enqueue(s, "draft_generate", {"assignment_id": a["assignment_id"], "user_id": user.id, "mode": body.mode,
                                        "model_config_id": body.model_config_id},
                  dedupe_key=f"draft:{a['assignment_id']}:{body.mode}:{body.model_config_id}", priority=45)
    s.commit()
    return {"job_id": jid}


class CompareRequest(BaseModel):
    model_ids: list[int] = Field(min_length=1, max_length=6)


@router.post("/articles/{article_id}/compare")
def compare_models(article_id: int, body: CompareRequest, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(active_user)):
    """One draft per model from the same packet and brief; none replaces the current version."""
    a = _assignment_of_article(s, article_id)
    check_account(s, user, a["account_id"], "draft")
    known = set(s.execute(text("SELECT id FROM ai_model_config WHERE enabled AND id = ANY(:i)"),
                          {"i": body.model_ids}).scalars())
    if missing := set(body.model_ids) - known:
        raise HTTPException(422, f"unknown or disabled models {sorted(missing)}")
    jobs = [enqueue(s, "draft_generate", {"assignment_id": a["assignment_id"], "user_id": user.id, "mode": "draft",
                                          "model_config_id": m, "compare": True},
                    dedupe_key=f"draft:{a['assignment_id']}:compare:{m}", priority=50) for m in body.model_ids]
    audit.record(s, "article.compare_models", "article", article_id, user_id=user.id, account_id=a["account_id"],
                 after={"models": body.model_ids})
    s.commit()
    return {"jobs": jobs}


@router.get("/articles/{article_id}/compare")
def compare_results(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    a = _assignment_of_article(s, article_id)
    check_account(s, user, a["account_id"], "view")
    return {
        "versions": rows(s, """
            SELECT v.id, v.version_no, v.model, v.agent_key, v.prompt_ref, v.headline, v.body, v.thread, v.angle,
                   v.guard_status, v.guard_report->'findings' AS findings, (v.guard_report->>'x_length')::int AS x_length,
                   v.created_at, c.cost_usd, c.latency_ms, c.input_tokens, c.output_tokens
            FROM article_version v LEFT JOIN ai_call_log c ON c.id = v.ai_call_id
            WHERE v.article_id = :a AND v.origin = 'ai' ORDER BY v.id DESC""", {"a": article_id}),
        "failures": rows(s, """SELECT model, status, error, created_at, cost_usd FROM ai_call_log
                               WHERE article_id = :a AND status <> 'ok' ORDER BY id DESC LIMIT 20""", {"a": article_id}),
        "pending": s.execute(text("""SELECT count(*) FROM job WHERE kind = 'draft_generate' AND status IN
                                     ('queued', 'running') AND (payload->>'assignment_id')::bigint = :g"""),
                             {"g": a["assignment_id"]}).scalar(),
    }


# ---- assignment desk ---------------------------------------------------------------------------------------------

@router.get("/assignments")
def assignment_desk(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                    status: str | None = None, mine: bool = False):
    vis = visible_accounts(s, user)
    return rows(s, f"""
        SELECT a.id, a.news_event_id, a.account_id, acc.handle, acc.language, a.agent_key, a.angle, a.format, a.status,
               a.source, a.needs_human, a.due_at, a.created_at, a.style_key, a.model_config_id,
               cu.username AS claimed_by, a.claimed_at, ru.username AS reviewer, a.reviewer_id,
               e.event_class, e.coin, e.priority, e.state AS event_state,
               ar.id AS article_id, ar.status AS article_status,
               (a.due_at IS NOT NULL AND a.due_at < now() AND a.status NOT IN ('done', 'rejected', 'cancelled')) AS overdue
        FROM assignment a JOIN editorial_account acc ON acc.id = a.account_id JOIN news_event e ON e.id = a.news_event_id
        LEFT JOIN article ar ON ar.assignment_id = a.id LEFT JOIN app_user cu ON cu.id = a.claimed_by
        LEFT JOIN app_user ru ON ru.id = a.reviewer_id
        WHERE true {"AND a.status = :st" if status else "AND a.status NOT IN ('done', 'rejected', 'cancelled')"}
              {"AND a.claimed_by = :me" if mine else ""} {"AND a.account_id = ANY(:vis)" if vis is not None else ""}
        ORDER BY overdue DESC, e.priority NULLS LAST, a.due_at NULLS LAST, a.id DESC LIMIT 300""",
        {"st": status, "me": user.id, "vis": vis})


class AssignmentUpdate(BaseModel):
    claim: bool | None = None
    due_at: datetime | None = None
    clear_due: bool = False
    reviewer_id: int | None = None
    agent_key: str | None = None
    style_key: str | None = None
    model_config_id: int | None = None


@router.patch("/assignments/{assignment_id}")
def update_assignment(assignment_id: int, body: AssignmentUpdate, s: Session = Depends(get_session),
                      user: CurrentUser = Depends(active_user)):
    a = one(s, "SELECT account_id FROM assignment WHERE id = :i", {"i": assignment_id})
    if a is None:
        raise HTTPException(404, "assignment not found")
    check_account(s, user, a["account_id"], "draft")
    sets, p = [], {"i": assignment_id}
    if body.claim is not None:
        sets.append("claimed_by = :u, claimed_at = CASE WHEN :claim THEN now() END")
        p.update(u=user.id if body.claim else None, claim=body.claim)
    if body.due_at or body.clear_due:
        sets.append("due_at = :due")
        p["due"] = None if body.clear_due else body.due_at
    for col in ("reviewer_id", "agent_key", "style_key", "model_config_id"):
        v = getattr(body, col)
        if v is not None:
            sets.append(f"{col} = :{col}")
            p[col] = v
    if body.agent_key and not s.execute(text("SELECT 1 FROM ai_agent_profile WHERE agent_key = :k AND active"),
                                        {"k": body.agent_key}).scalar():
        raise HTTPException(422, "unknown agent")
    if not sets:
        raise HTTPException(422, "nothing to change")
    s.execute(text(f"UPDATE assignment SET {', '.join(sets)} WHERE id = :i"), p)
    audit.record(s, "assignment.update", "assignment", assignment_id, user_id=user.id, account_id=a["account_id"],
                 after=body.model_dump(mode="json", exclude_none=True))
    s.commit()
    return {"ok": True}


# ---- publication calendar ----------------------------------------------------------------------------------------

@router.get("/plans")
def list_plans(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
               days: int = Query(14, ge=1, le=90)):
    vis = visible_accounts(s, user)
    t = now()
    return {"plans": rows(s, f"""
        SELECT p.id, p.article_id, p.account_id, acc.handle, acc.timezone, acc.min_gap_minutes, p.planned_at, p.status,
               p.note, p.claimed_at, ar.status AS article_status, v.headline, e.event_class, e.coin,
               (e.detected_at < now() - interval '24 hours' AND e.column_key IS NULL) AS aging
        FROM publication_plan p JOIN editorial_account acc ON acc.id = p.account_id JOIN article ar ON ar.id = p.article_id
        JOIN news_event e ON e.id = ar.news_event_id LEFT JOIN article_version v ON v.id = ar.current_version_id
        WHERE p.planned_at BETWEEN :a AND :b {"AND p.account_id = ANY(:vis)" if vis is not None else ""}
        ORDER BY p.planned_at""", {"a": t - timedelta(days=2), "b": t + timedelta(days=days), "vis": vis}),
            "unscheduled": rows(s, f"""
        SELECT ar.id AS article_id, acc.handle, v.headline, e.event_class, e.coin, ar.updated_at
        FROM article ar JOIN editorial_account acc ON acc.id = ar.account_id JOIN news_event e ON e.id = ar.news_event_id
        LEFT JOIN article_version v ON v.id = ar.current_version_id
        WHERE ar.status = 'approved' AND NOT EXISTS (SELECT 1 FROM publication_plan p WHERE p.article_id = ar.id
                                                     AND p.status IN ('planned', 'claimed'))
              {"AND ar.account_id = ANY(:vis)" if vis is not None else ""}
        ORDER BY ar.updated_at""", {"vis": vis})}


class PlanIn(BaseModel):
    planned_at: datetime
    note: str = Field("", max_length=500)
    force: bool = False


@router.post("/articles/{article_id}/plan")
def plan_article(article_id: int, body: PlanIn, s: Session = Depends(get_session),
                 user: CurrentUser = Depends(active_user)):
    art = one(s, """SELECT ar.status, ar.account_id, acc.min_gap_minutes FROM article ar
                    JOIN editorial_account acc ON acc.id = ar.account_id WHERE ar.id = :a""", {"a": article_id})
    if art is None:
        raise HTTPException(404, "article not found")
    check_account(s, user, art["account_id"], "publish")
    if art["status"] != "approved":
        raise HTTPException(409, "only approved articles can be scheduled")
    gap = timedelta(minutes=art["min_gap_minutes"])
    near = s.execute(text("""SELECT planned_at FROM publication_plan WHERE account_id = :acc AND status IN
                             ('planned', 'claimed') AND planned_at BETWEEN :a AND :b
                             UNION ALL SELECT published_at FROM publication_record WHERE account_id = :acc
                             AND status = 'published' AND published_at BETWEEN :a AND :b"""),
                     {"acc": art["account_id"], "a": body.planned_at - gap, "b": body.planned_at + gap}).scalars().all()
    if near and not body.force:
        raise HTTPException(409, f"another post on this account within {art['min_gap_minutes']} minutes")
    s.execute(text("""UPDATE publication_plan SET status = 'cancelled' WHERE article_id = :a
                      AND status IN ('planned', 'claimed')"""), {"a": article_id})
    pid = s.execute(text("""INSERT INTO publication_plan (article_id, account_id, planned_at, created_by, note)
                            VALUES (:a, :acc, :t, :u, :n) RETURNING id"""),
                    {"a": article_id, "acc": art["account_id"], "t": body.planned_at, "u": user.id, "n": body.note}
                    ).scalar_one()
    audit.record(s, "plan.create", "plan", pid, user_id=user.id, account_id=art["account_id"],
                 after={"article_id": article_id, "planned_at": body.planned_at.isoformat()})
    s.commit()
    return {"plan_id": pid}


@router.post("/plans/{plan_id}/cancel")
def cancel_plan(plan_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    p = one(s, "SELECT account_id FROM publication_plan WHERE id = :i", {"i": plan_id})
    if p is None:
        raise HTTPException(404, "plan not found")
    check_account(s, user, p["account_id"], "publish")
    s.execute(text("UPDATE publication_plan SET status = 'cancelled' WHERE id = :i AND status IN ('planned', 'claimed')"),
              {"i": plan_id})
    audit.record(s, "plan.cancel", "plan", plan_id, user_id=user.id, account_id=p["account_id"])
    s.commit()
    return {"ok": True}


# ---- RPA hand-off: an external publisher pulls due plans and reports back ------------------------------------------

def _rpa(s: Session, authorization: str | None) -> None:
    token = settings_store.get_secret(s, "rpa_token")
    if not token or not authorization or not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "RPA token required")


@router.post("/settings/rpa-token")
def new_rpa_token(s: Session = Depends(get_session), user: CurrentUser = Depends(require_roles("admin"))):
    token = secrets.token_urlsafe(32)
    settings_store.put_secret(s, "rpa_token", token)
    audit.record(s, "rpa.token_rotated", "setting", "rpa_token", user_id=user.id)
    s.commit()
    return {"token": token, "note": "shown once; the RPA sends it as Authorization: Bearer <token>"}


@router.get("/rpa/next")
def rpa_next(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    """The next due plan on an RPA-enabled, authorized account. Claiming it stops anyone else taking it."""
    _rpa(s, authorization)
    if settings_store.emergency_stopped(s):
        return {"plan": None, "reason": "emergency stop"}
    t = now()
    p = one(s, """
        SELECT p.id, p.article_id, p.account_id, acc.handle, p.planned_at
        FROM publication_plan p JOIN editorial_account acc ON acc.id = p.account_id
        WHERE p.status = 'planned' AND p.planned_at <= :t AND acc.rpa_enabled AND acc.status = 'active'
          AND acc.authorization_status = 'authorized' AND (acc.authorization_expires_at IS NULL OR acc.authorization_expires_at > :t)
        ORDER BY p.planned_at LIMIT 1 FOR UPDATE OF p SKIP LOCKED""", {"t": t})
    if p is None:
        return {"plan": None}
    if blockers := pub.publish_blockers(s, p["account_id"], t):
        return {"plan": None, "reason": "; ".join(blockers)}
    v = one(s, """SELECT v.body, v.thread, v.fact_packet_id FROM article a JOIN article_version v ON v.id = a.current_version_id
                  WHERE a.id = :a""", {"a": p["article_id"]})
    s.execute(text("UPDATE publication_plan SET status = 'claimed', claimed_at = :t WHERE id = :i"), {"t": t, "i": p["id"]})
    audit.record(s, "rpa.claim", "plan", p["id"], actor_kind="system", account_id=p["account_id"])
    s.commit()
    assets = rows(s, """SELECT id, template_key, language FROM media_asset WHERE fact_packet_id = :f AND kind = 'card_png'""",
                  {"f": v["fact_packet_id"]})
    return {"plan": {**p, "text": "\n\n".join([v["body"], *(v["thread"] or [])]), "posts": [v["body"], *(v["thread"] or [])],
                     "assets": assets}}


class RpaResult(BaseModel):
    ok: bool
    x_url: str | None = Field(None, max_length=300)
    published_at: datetime | None = None
    final_text: str | None = Field(None, max_length=8000)
    error: str | None = Field(None, max_length=1000)


@router.post("/rpa/plans/{plan_id}/result")
def rpa_result(plan_id: int, body: RpaResult, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    _rpa(s, authorization)
    p = one(s, "SELECT * FROM publication_plan WHERE id = :i AND status = 'claimed' FOR UPDATE", {"i": plan_id})
    if p is None:
        raise HTTPException(409, "plan is not claimed")
    if not body.ok:
        s.execute(text("UPDATE publication_plan SET status = 'planned', claimed_at = NULL, note = :n WHERE id = :i"),
                  {"n": f"RPA failed: {body.error or ''}"[:500], "i": plan_id})
        audit.record(s, "rpa.failed", "plan", plan_id, actor_kind="system", account_id=p["account_id"], reason=body.error)
        s.commit()
        return {"ok": True, "requeued": True}
    if not body.x_url or not body.published_at:
        raise HTTPException(422, "x_url and published_at are required")
    # recorded in the name of whoever scheduled it; every check of a manual record applies
    pid = pub.mark_published(s, p["article_id"], p["created_by"], body.x_url, body.published_at, now(), body.final_text)
    audit.record(s, "rpa.published", "plan", plan_id, actor_kind="system", account_id=p["account_id"],
                 after={"publication_id": pid})
    s.commit()
    return {"ok": True, "publication_id": pid}


# ---- style library -----------------------------------------------------------------------------------------------

class StyleIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,48}$")
    language: Literal["zh", "en"]
    name: str = Field(max_length=100)
    guide: str = Field(min_length=10, max_length=4000)
    structure: dict = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list)


@router.post("/styles")
def new_style_version(body: StyleIn, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    v = s.execute(text("SELECT coalesce(max(version), 0) + 1 FROM editorial_style WHERE key = :k"), {"k": body.key}).scalar()
    s.execute(text("""INSERT INTO editorial_style (key, version, language, name, guide, structure, forbidden)
                      VALUES (:k, :v, :l, :n, :g, CAST(:s AS jsonb), CAST(:f AS jsonb))"""),
              {"k": body.key, "v": v, "l": body.language, "n": body.name, "g": body.guide,
               "s": json.dumps(body.structure, ensure_ascii=False), "f": json.dumps(body.forbidden, ensure_ascii=False)})
    audit.record(s, "style.version", "style", f"{body.key}@v{v}", user_id=user.id)
    s.commit()
    return {"key": body.key, "version": v}


# ---- trial drafts: try models on any event without touching its workflow -------------------------------------------

class TrialIn(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=6)
    model_ids: list[int] = Field(default_factory=list, max_length=6)
    angle: str = Field("", max_length=300)


@router.post("/events/{event_id}/trial")
def trial_drafts(event_id: int, body: TrialIn, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """One trial article per account (reused on later trials), one AI draft per chosen model (or the role's default).
    Works on paused accounts and blocked events; trials never change the event and cannot be submitted for review."""
    from nr.desk import assignment
    ev = one(s, "SELECT state FROM news_event WHERE id = :e", {"e": event_id})
    if ev is None:
        raise HTTPException(404, "event not found")
    if ev["state"] == "archived":
        raise HTTPException(409, "event is archived")
    if body.model_ids:
        known = set(s.execute(text("SELECT id FROM ai_model_config WHERE enabled AND id = ANY(:i)"),
                              {"i": body.model_ids}).scalars())
        if missing := set(body.model_ids) - known:
            raise HTTPException(422, f"unknown or disabled models {sorted(missing)}")
    out = []
    for acc in dict.fromkeys(body.account_ids):
        check_account(s, user, acc, "draft")
        aid = s.execute(text("""SELECT id FROM assignment WHERE news_event_id = :e AND account_id = :a AND source = 'trial'
                                AND status IN ('accepted', 'drafting') ORDER BY id DESC LIMIT 1"""),
                        {"e": event_id, "a": acc}).scalar()
        if aid is None:
            aid = assignment.create(s, event_id, acc, source="trial", status="accepted", user_id=user.id,
                                    angle=body.angle, reason="trial")
        art = s.execute(text("SELECT id FROM article WHERE assignment_id = :a"), {"a": aid}).scalar() \
            or assignment.accept(s, aid, user.id)
        models = body.model_ids or [None]
        jobs = [enqueue(s, "draft_generate", {"assignment_id": aid, "user_id": user.id, "mode": "draft",
                                              "model_config_id": m, "compare": len(models) > 1},
                        dedupe_key=f"draft:{aid}:trial:{m}", priority=35) for m in models]
        out.append({"account_id": acc, "article_id": art, "jobs": jobs})
    audit.record(s, "article.trial", "news_event", event_id, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"articles": out}


# ---- trash, bulk actions, export, AI draft overview ---------------------------------------------------------------

def _article(s: Session, article_id: int) -> dict:
    a = one(s, "SELECT id, account_id, status, deleted_at, current_version_id FROM article WHERE id = :a", {"a": article_id})
    if a is None:
        raise HTTPException(404, "article not found")
    return a


def trash_article(s: Session, user: CurrentUser, article_id: int, reason: str) -> None:
    a = _article(s, article_id)
    check_account(s, user, a["account_id"], "draft")
    if a["status"] in ("published", "retracted"):
        raise HTTPException(409, "published articles are retracted, not deleted")
    s.execute(text("""UPDATE article SET deleted_at = now(), deleted_by = :u, deleted_reason = :r,
                             status = CASE WHEN status IN ('in_review', 'approved') THEN 'killed' ELSE status END,
                             updated_at = now() WHERE id = :a"""), {"u": user.id, "r": reason, "a": article_id})
    s.execute(text("UPDATE publication_plan SET status = 'cancelled' WHERE article_id = :a AND status IN ('planned', 'claimed')"),
              {"a": article_id})
    audit.record(s, "article.trash", "article", article_id, user_id=user.id, account_id=a["account_id"], reason=reason)


def restore_article(s: Session, user: CurrentUser, article_id: int) -> None:
    a = _article(s, article_id)
    check_account(s, user, a["account_id"], "draft")
    s.execute(text("""UPDATE article SET deleted_at = NULL, deleted_by = NULL, deleted_reason = NULL, updated_at = now(),
                             status = CASE WHEN status = 'killed' THEN
                                 CASE WHEN current_version_id IS NULL THEN 'drafting' ELSE 'draft_ready' END ELSE status END
                      WHERE id = :a"""), {"a": article_id})
    audit.record(s, "article.restore", "article", article_id, user_id=user.id, account_id=a["account_id"])


class TrashIn(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/articles/{article_id}/trash")
def trash(article_id: int, body: TrashIn, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    trash_article(s, user, article_id, body.reason)
    s.commit()
    return {"ok": True}


@router.post("/articles/{article_id}/restore")
def restore(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    restore_article(s, user, article_id)
    s.commit()
    return {"ok": True}


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["trash", "restore", "submit", "kill"]
    reason: str = Field("", max_length=500)


@router.post("/articles/bulk")
def bulk(body: BulkIn, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """Each article is handled on its own: one refusal does not undo the others."""
    done, failed = [], []
    for aid in dict.fromkeys(body.ids):
        try:
            with s.begin_nested():
                if body.action == "trash":
                    trash_article(s, user, aid, body.reason)
                elif body.action == "restore":
                    restore_article(s, user, aid)
                elif body.action == "submit":
                    check_account(s, user, _article(s, aid)["account_id"], "draft")
                    pub.submit(s, aid, user.id)
                else:
                    check_account(s, user, _article(s, aid)["account_id"], "review")
                    pub.kill(s, aid, user.id, body.reason)
            done.append(aid)
        except HTTPException as exc:
            failed.append({"id": aid, "error": exc.detail if isinstance(exc.detail, str) else str(exc.detail)})
    s.commit()
    return {"done": done, "failed": failed}


@router.get("/articles/{article_id}/export")
def export_article(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """The current version as Markdown, with the facts it cites and the guard verdict."""
    from fastapi.responses import PlainTextResponse
    a = _article(s, article_id)
    check_account(s, user, a["account_id"], "view")
    v = one(s, """SELECT v.*, acc.handle, acc.platform, p.content FROM article ar JOIN article_version v ON v.id = ar.current_version_id
                  JOIN editorial_account acc ON acc.id = ar.account_id JOIN fact_packet p ON p.id = v.fact_packet_id
                  WHERE ar.id = :a""", {"a": article_id})
    if v is None:
        raise HTTPException(404, "no version yet")
    facts = {f["id"]: f for f in v["content"]["facts"]}
    lines = [f"# {v['headline'] or '(no headline)'}", "", f"@{v['handle']} · {v['platform']} · v{v['version_no']} · "
             f"{v['origin']}{' · ' + v['model'] if v['model'] else ''} · guard {v['guard_status']}", "", v["body"]]
    lines += [f"\n{t}" for t in (v["thread"] or [])]
    lines += ["", "## Facts cited", *[f"- {fid}: {(facts.get(fid) or {}).get('display', {}).get('zh', ['?'])[0]}"
                                     for fid in v["facts_used"]]]
    if v["guard_report"]["findings"]:
        lines += ["", "## Guard findings", *[f"- [{f['code']}] {f['text']} — {f['detail']}"
                                            for f in v["guard_report"]["findings"]]]
    return PlainTextResponse("\n".join(lines), headers={"content-disposition": f'attachment; filename="article-{article_id}.md"'})


@router.get("/drafts/ai")
def ai_drafts(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
              days: int = Query(7, ge=1, le=90), model: str | None = None, guard: str | None = None):
    vis = visible_accounts(s, user)
    since = now() - timedelta(days=days)
    p = {"since": since, "vis": vis, "model": model, "guard": guard}
    return {
        "versions": rows(s, f"""
            SELECT v.id, v.article_id, v.version_no, v.model, v.agent_key, v.prompt_ref, v.headline, v.body, v.guard_status,
                   (v.guard_report->>'x_length')::int AS length, v.created_at, (ar.current_version_id = v.id) AS is_current,
                   ar.status AS article_status, ar.deleted_at IS NOT NULL AS deleted, asg.source, acc.handle, acc.platform,
                   e.id AS event_id, e.event_class, e.coin, c.cost_usd, c.latency_ms
            FROM article_version v JOIN article ar ON ar.id = v.article_id JOIN editorial_account acc ON acc.id = ar.account_id
            JOIN news_event e ON e.id = ar.news_event_id LEFT JOIN assignment asg ON asg.id = ar.assignment_id
            LEFT JOIN ai_call_log c ON c.id = v.ai_call_id
            WHERE v.origin = 'ai' AND v.created_at > :since {"AND ar.account_id = ANY(:vis)" if vis is not None else ""}
                  {"AND v.model = :model" if model else ""} {"AND v.guard_status = :guard" if guard else ""}
            ORDER BY v.id DESC LIMIT 300""", p),
        "failures": rows(s, """
            SELECT c.created_at, c.purpose, c.model, c.status, c.error, c.article_id, c.news_event_id
            FROM ai_call_log c WHERE c.status <> 'ok' AND c.created_at > :since ORDER BY c.id DESC LIMIT 50""",
            {"since": since}),
        "waiting": rows(s, """SELECT kind, count(*) AS n, max(last_error) AS last_error FROM job
                              WHERE kind IN ('eic_evaluate', 'draft_generate') AND status IN ('queued', 'running')
                              GROUP BY kind"""),
        "models": rows(s, "SELECT DISTINCT model FROM article_version WHERE origin = 'ai' AND model IS NOT NULL"),
    }
