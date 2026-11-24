"""Articles, versions, review, publication records, performance entry and media assets."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import audit
from nr.api.common import now, one, rows
from nr.articles import versions
from nr.articles.versions import Draft
from nr.auth.deps import active_user, check_account, visible_accounts
from nr.auth.sessions import CurrentUser
from nr.cards.render import TEMPLATES, render_for_packet
from nr.db import get_session
from nr.events.states import InvalidTransition, transition
from nr.publish import service as pub

router = APIRouter(prefix="/api", tags=["articles"])


def _account_of(s: Session, article_id: int) -> int:
    acc = s.execute(text("SELECT account_id FROM article WHERE id = :a"), {"a": article_id}).scalar()
    if acc is None:
        raise HTTPException(404, "article not found")
    return acc


@router.get("/articles")
def list_articles(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                  account_id: int | None = None, status: str | None = None, language: str | None = None,
                  q: str | None = Query(None, max_length=200), event_class: str | None = None,
                  since: datetime | None = None, origin: str | None = None, guard: str | None = None,
                  model: str | None = Query(None, max_length=128), trial: bool | None = None, trash: bool = False,
                  limit: int = Query(100, le=500), offset: int = 0):
    """The content library: every draft is kept and searchable (headline and body of the current version)."""
    where, p = ["a.deleted_at IS NOT NULL" if trash else "a.deleted_at IS NULL"], {"n": limit, "o": offset}
    for col, val in (("v.origin", origin), ("v.guard_status", guard), ("v.model", model)):
        if val:
            where.append(f"{col} = :{col.split('.')[1]}")
            p[col.split(".")[1]] = val
    if trial is not None:
        where.append(("" if trial else "NOT ") + "EXISTS (SELECT 1 FROM assignment t WHERE t.id = a.assignment_id "
                     "AND t.source = 'trial')")
    vis = visible_accounts(s, user)
    if vis is not None:
        where.append("a.account_id = ANY(:vis)")
        p["vis"] = vis
    for col, val in (("account_id", account_id), ("status", status), ("language", language)):
        if val is not None:
            where.append(f"a.{col} = :{col}")
            p[col] = val
    if event_class:
        where.append("e.event_class = :ec")
        p["ec"] = event_class
    if since:
        where.append("a.created_at >= :since")
        p["since"] = since
    if q:
        where.append("(v.headline ILIKE :q OR v.body ILIKE :q OR v.angle ILIKE :q)")
        p["q"] = f"%{q}%"
    cond = " AND ".join(where)
    base = f"""FROM article a JOIN news_event e ON e.id = a.news_event_id JOIN editorial_account acc ON acc.id = a.account_id
               LEFT JOIN article_version v ON v.id = a.current_version_id
               LEFT JOIN assignment asg ON asg.id = a.assignment_id WHERE {cond}"""
    return {"total": s.execute(text(f"SELECT count(*) {base}"), p).scalar(),
            "rows": rows(s, f"""SELECT a.id, a.news_event_id, a.account_id, acc.handle, a.language, a.status, a.updated_at,
                                       e.event_class, e.coin, v.headline, v.angle, v.origin, v.guard_status,
                                       v.fact_packet_version, v.version_no, v.model, asg.source, a.deleted_at,
                                       a.deleted_reason, acc.platform,
                                       (SELECT count(*) FROM article_version x WHERE x.article_id = a.id) AS versions
                                {base} ORDER BY a.updated_at DESC LIMIT :n OFFSET :o""", p)}


@router.get("/articles/{article_id}")
def article_detail(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    check_account(s, user, _account_of(s, article_id), "view")
    art = one(s, """SELECT a.*, acc.handle, acc.display_name AS account_name, acc.long_post_allowed, acc.platform,
                           acc.authorization_status, acc.authorization_expires_at, e.current_fact_packet_id,
                           e.current_fact_version, e.gate_status, e.event_class, e.coin, asg.source,
                           (SELECT count(*) FROM job j WHERE j.kind = 'draft_generate' AND j.status IN ('queued', 'running')
                              AND (j.payload->>'assignment_id')::bigint = a.assignment_id) AS pending_jobs
                    FROM article a JOIN editorial_account acc ON acc.id = a.account_id
                    LEFT JOIN assignment asg ON asg.id = a.assignment_id
                    JOIN news_event e ON e.id = a.news_event_id WHERE a.id = :a""", {"a": article_id})
    return {"article": art,
            "versions": rows(s, """SELECT v.id, v.version_no, v.origin, v.parent_version_id, u.username AS author,
                                          v.agent_key, v.model, v.prompt_ref, v.fact_packet_version, v.headline, v.body,
                                          v.thread, v.alt_headlines, v.angle, v.added_value, v.difference_from_others,
                                          v.why_shareable, v.risks, v.facts_used, v.guard_status, v.guard_report,
                                          v.created_at, v.fact_packet_id, v.why_now, v.open_question, v.lede_fact_ids,
                                          v.card_template, (SELECT m.id FROM media_asset m JOIN article ar ON ar.id = v.article_id
                                            WHERE m.fact_packet_id = v.fact_packet_id AND m.template_key = v.card_template
                                              AND m.language = ar.language AND m.kind = 'card_png'
                                            ORDER BY m.id DESC LIMIT 1) AS card_asset_id
                                   FROM article_version v LEFT JOIN app_user u ON u.id = v.author_user_id
                                   WHERE v.article_id = :a ORDER BY v.version_no DESC""", {"a": article_id}),
            "publications": rows(s, "SELECT * FROM publication_record WHERE article_id = :a ORDER BY id",
                                 {"a": article_id}),
            "audit": rows(s, """SELECT l.at, l.action, u.username, l.reason FROM audit_log l
                                LEFT JOIN app_user u ON u.id = l.actor_user_id
                                WHERE l.object_type = 'article' AND l.object_id = :a ORDER BY l.at""",
                          {"a": str(article_id)})}


class VersionIn(BaseModel):
    headline: str = Field("", max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    thread: list[str] | None = None
    alt_headlines: list[str] | None = None
    angle: str = Field("", max_length=300)
    added_value: str = Field("", max_length=500)
    facts_used: list[str] = Field(default_factory=list)
    fact_packet_id: int | None = None


@router.post("/articles/{article_id}/versions")
def save_human_version(article_id: int, body: VersionIn, s: Session = Depends(get_session),
                       user: CurrentUser = Depends(active_user)):
    acc = _account_of(s, article_id)
    check_account(s, user, acc, "draft")
    v = versions.save_version(s, article_id, Draft(body=body.body, headline=body.headline, thread=body.thread,
                                                   alt_headlines=body.alt_headlines, angle=body.angle,
                                                   added_value=body.added_value, facts_used=body.facts_used),
                              origin="human", user_id=user.id, fact_packet_id=body.fact_packet_id)
    audit.record(s, "article.version", "article", article_id, user_id=user.id, account_id=acc,
                 after={"version_id": v["id"], "guard": v["guard_status"]})
    s.commit()
    return v


class GuardCheck(BaseModel):
    headline: str = ""
    body: str = ""
    thread: list[str] | None = None
    facts_used: list[str] = Field(default_factory=list)


@router.post("/articles/{article_id}/guard")
def dry_run_guard(article_id: int, body: GuardCheck, s: Session = Depends(get_session),
                  user: CurrentUser = Depends(active_user)):
    """Check text while editing, without saving a version."""
    check_account(s, user, _account_of(s, article_id), "view")
    art = versions.load(s, article_id)
    return versions.guard(s, art, art["current_fact_packet_id"], versions.parts_of(body.headline, body.body, body.thread),
                          body.facts_used)


class VersionRef(BaseModel):
    version_id: int


@router.post("/articles/{article_id}/accept-version")
def accept_version(article_id: int, body: VersionRef, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(active_user)):
    acc = _account_of(s, article_id)
    check_account(s, user, acc, "draft")
    versions.accept_version(s, article_id, body.version_id)
    audit.record(s, "article.accept_version", "article", article_id, user_id=user.id, account_id=acc,
                 after={"version_id": body.version_id})
    s.commit()
    return {"ok": True}


class ReviewAction(BaseModel):
    action: Literal["submit", "approve", "quick_approve", "request_changes", "kill"]
    note: str = Field("", max_length=2000)
    ack_duplicate: bool = False


@router.post("/articles/{article_id}/review")
def review(article_id: int, body: ReviewAction, s: Session = Depends(get_session),
           user: CurrentUser = Depends(active_user)):
    acc = _account_of(s, article_id)
    out = None
    if body.action == "submit":
        check_account(s, user, acc, "draft")
        pub.submit(s, article_id, user.id)
    elif body.action == "approve":
        check_account(s, user, acc, "review")
        out = pub.approve(s, article_id, user.id, body.ack_duplicate)
    elif body.action == "quick_approve":  # straight from a finished draft to ready-to-post
        check_account(s, user, acc, "review")
        out = pub.quick_approve(s, article_id, user.id, body.ack_duplicate)
    elif body.action == "request_changes":
        check_account(s, user, acc, "review")
        pub.request_changes(s, article_id, user.id, body.note)
    else:
        check_account(s, user, acc, "review")
        pub.kill(s, article_id, user.id, body.note)
    s.commit()
    return {"ok": True, "guard": out}


@router.post("/articles/{article_id}/copied")
def copied(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """The operator copied the approved text to post it by hand: recorded for the audit trail."""
    acc = _account_of(s, article_id)
    check_account(s, user, acc, "publish")
    audit.record(s, "article.copy_text", "article", article_id, user_id=user.id, account_id=acc)
    s.commit()
    return {"ok": True, "blockers": pub.publish_blockers(s, acc, now())}


class PublishIn(BaseModel):
    x_url: str | None = Field(None, max_length=300)  # optional for a manual record: "I posted it"
    published_at: datetime | None = None  # now when empty
    final_text: str | None = Field(None, max_length=8000)
    reply_url: str | None = Field(None, max_length=300)
    allow_expired: bool = False


@router.post("/articles/{article_id}/publish")
def record_publication(article_id: int, body: PublishIn, s: Session = Depends(get_session),
                       user: CurrentUser = Depends(active_user)):
    check_account(s, user, _account_of(s, article_id), "publish")
    pid = pub.mark_published(s, article_id, user.id, body.x_url, body.published_at or now(), now(), body.final_text,
                             reply_url=body.reply_url, allow_expired=body.allow_expired)
    s.commit()
    return {"publication_id": pid}


@router.post("/articles/{article_id}/promote")
def promote_trial(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """A trial draft worth posting becomes the account's real article for the event, versions and all."""
    acc = _account_of(s, article_id)
    check_account(s, user, acc, "draft")
    a = one(s, """SELECT asg.id, asg.source, asg.news_event_id, asg.account_id FROM article ar
                  JOIN assignment asg ON asg.id = ar.assignment_id WHERE ar.id = :a""", {"a": article_id})
    if a is None or a["source"] != "trial":
        raise HTTPException(409, "only a trial draft can be promoted")
    if s.execute(text("""SELECT 1 FROM assignment WHERE news_event_id = :e AND account_id = :acc AND source <> 'trial'
                         AND status NOT IN ('rejected', 'cancelled')"""),
                 {"e": a["news_event_id"], "acc": a["account_id"]}).scalar():
        raise HTTPException(409, "this account already has an article for the event: copy the text into it instead")
    s.execute(text("""UPDATE assignment SET source = 'editor', reason = reason || ' (promoted from trial)'
                      WHERE id = :i"""), {"i": a["id"]})
    try:
        transition(s, a["news_event_id"], "assigned", actor_kind="user", user_id=user.id, reason="trial promoted")
    except InvalidTransition:
        pass
    audit.record(s, "article.promote", "article", article_id, user_id=user.id, account_id=acc)
    s.commit()
    return {"ok": True}


@router.get("/publications")
def list_publications(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                      account_id: int | None = None, limit: int = Query(100, le=500)):
    vis = visible_accounts(s, user)
    return rows(s, f"""SELECT pr.*, acc.handle, a.news_event_id, a.language, v.version_no, v.headline,
                              (SELECT row_to_json(ps) FROM performance_snapshot ps WHERE ps.publication_id = pr.id
                               ORDER BY ps.captured_at DESC LIMIT 1) AS latest_performance
                       FROM publication_record pr JOIN editorial_account acc ON acc.id = pr.account_id
                       JOIN article a ON a.id = pr.article_id JOIN article_version v ON v.id = pr.article_version_id
                       WHERE true {"AND pr.account_id = :acc" if account_id else ""}
                             {"AND pr.account_id = ANY(:vis)" if vis is not None else ""}
                       ORDER BY pr.published_at DESC LIMIT :n""", {"acc": account_id, "vis": vis, "n": limit})


def _publication_account(s: Session, pid: int) -> int:
    acc = s.execute(text("SELECT account_id FROM publication_record WHERE id = :p"), {"p": pid}).scalar()
    if acc is None:
        raise HTTPException(404, "publication not found")
    return acc


class Retract(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/publications/{pid}/retract")
def retract(pid: int, body: Retract, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    check_account(s, user, _publication_account(s, pid), "publish")
    pub.retract(s, pid, user.id, body.reason, now())
    s.commit()
    return {"ok": True}


class Performance(BaseModel):
    captured_at: datetime
    impressions: int | None = None
    likes: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    replies: int | None = None
    bookmarks: int | None = None
    profile_visits: int | None = None
    follows: int | None = None
    link_clicks: int | None = None


@router.post("/publications/{pid}/performance")
def add_performance(pid: int, body: Performance, s: Session = Depends(get_session),
                    user: CurrentUser = Depends(active_user)):
    """Manual entry: X metrics are typed in (or imported) from what the account owner can see."""
    check_account(s, user, _publication_account(s, pid), "publish")
    d = body.model_dump()
    s.execute(text(f"""INSERT INTO performance_snapshot (publication_id, source, entered_by, {", ".join(d)})
                       VALUES (:pid, 'manual', :u, {", ".join(":" + k for k in d)})"""), {**d, "pid": pid, "u": user.id})
    s.commit()
    return {"ok": True}


class RenderIn(BaseModel):
    fact_packet_id: int
    template: str
    language: Literal["zh", "en"]


@router.post("/assets/render")
def render_card(body: RenderIn, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    from nr.cards.v2 import V2_BUILDERS
    if body.template not in {*TEMPLATES, *V2_BUILDERS}:
        raise HTTPException(422, f"template must be one of {sorted({*TEMPLATES, *V2_BUILDERS})}")
    if not s.execute(text("SELECT 1 FROM fact_packet WHERE id = :p"), {"p": body.fact_packet_id}).scalar():
        raise HTTPException(404, "fact packet not found")
    try:
        return render_for_packet(sessionmaker(bind=s.get_bind(), expire_on_commit=False), body.fact_packet_id,
                                 body.template, body.language)
    except ValueError as exc:  # the packet has nothing for this card
        raise HTTPException(422, str(exc)) from exc


@router.get("/assets")
def list_assets(fact_packet_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    return rows(s, """SELECT id, kind, template_key, template_version, language, brand, data_hash, sha256, created_at
                      FROM media_asset WHERE fact_packet_id = :p ORDER BY id""", {"p": fact_packet_id})


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    a = one(s, "SELECT kind, file_path, template_key, language FROM media_asset WHERE id = :i", {"i": asset_id})
    if a is None or not Path(a["file_path"]).exists():
        raise HTTPException(404, "asset not found")
    ext = "png" if a["kind"] == "card_png" else "svg"
    return FileResponse(a["file_path"], media_type="image/png" if ext == "png" else "image/svg+xml",
                        filename=f"{a['template_key']}-{asset_id}-{a['language']}.{ext}")
