"""The news desk: events, their evidence and fact packets, the editor-in-chief's evaluations, assignments, stories
and corrections. Reading needs only a signed-in user; changing anything is role- and account-checked."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now, one, rows
from nr.auth.deps import active_user, check_account, require_roles
from nr.auth.rbac import EVENT_DESK_ROLES
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.desk import assignment
from nr.events.states import STATES, InvalidTransition, transition
from nr.jobs.queue import enqueue

router = APIRouter(prefix="/api", tags=["desk"])
EVENT_COLS = """e.id, e.event_class, e.venue, e.coin, e.direction, e.escalation_level, e.liquidation_status, e.state,
    e.state_changed_at, e.score, e.score_breakdown, e.gate_status, e.blocking_issues, e.priority, e.current_fact_version,
    e.source_event_count, e.detected_at, e.updated_at, e.story_id, t.trader_key, t.display_name, t.public_label"""


@router.get("/events")
def list_events(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                state: str | None = None, event_class: str | None = None, venue: str | None = None,
                coin: str | None = Query(None, max_length=48), min_score: float = 0, gate: str | None = None,
                include_dismissed: bool = False, limit: int = Query(100, le=500), offset: int = 0):
    where, p = ["e.score >= :ms OR e.score IS NULL"], {"ms": min_score, "n": limit, "o": offset}
    if state:
        if state not in STATES:
            raise HTTPException(422, "unknown state")
        where.append("e.state = :st")
        p["st"] = state
    elif not include_dismissed:
        where.append("e.state NOT IN ('dismissed', 'archived')")
    for col, val in (("event_class", event_class), ("venue", venue), ("coin", coin), ("gate_status", gate)):
        if val:
            where.append(f"e.{col} = :{col}")
            p[col] = val
    cond = " AND ".join(f"({w})" for w in where)
    total = s.execute(text(f"SELECT count(*) FROM news_event e WHERE {cond}"), p).scalar()
    items = rows(s, f"""SELECT {EVENT_COLS} FROM news_event e JOIN trader t ON t.id = e.trader_id WHERE {cond}
                        ORDER BY e.detected_at DESC, e.id DESC LIMIT :n OFFSET :o""", p)
    return {"total": total, "rows": items}


@router.get("/events/{event_id}")
def event_detail(event_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    ev = one(s, f"SELECT {EVENT_COLS}, e.episode_id FROM news_event e JOIN trader t ON t.id = e.trader_id "
                "WHERE e.id = :e", {"e": event_id})
    if ev is None:
        raise HTTPException(404, "event not found")
    return {
        "event": ev,
        "packet": one(s, """SELECT id, version, built_at, content, content_hash, gate_status, blocking_issues,
                                   change_summary FROM fact_packet WHERE id = (SELECT current_fact_packet_id
                                   FROM news_event WHERE id = :e)""", {"e": event_id}),
        "packets": rows(s, """SELECT id, version, built_at, gate_status, change_summary, content_hash FROM fact_packet
                              WHERE news_event_id = :e ORDER BY version DESC""", {"e": event_id}),
        "episode": one(s, "SELECT * FROM position_episode WHERE id = :p", {"p": ev["episode_id"]}),
        "source_events": rows(s, """SELECT se.id, se.source_event_id, se.kind, se.side, se.szi_before, se.szi_after,
                                           se.notional_change, se.mark_px, se.entry_px, se.leverage, se.liq_px,
                                           se.event_time, se.time_precision, se.window_start, se.observed_at
                                    FROM source_event se WHERE se.episode_id = :p ORDER BY se.id""",
                                 {"p": ev["episode_id"]}),
        "evidence": rows(s, """SELECT kind, ref_id, content_hash, captured_at FROM event_evidence WHERE news_event_id = :e
                               ORDER BY captured_at""", {"e": event_id}),
        "history": rows(s, """SELECT h.from_state, h.to_state, h.at, h.actor_kind, u.username, h.reason
                              FROM event_state_history h LEFT JOIN app_user u ON u.id = h.actor_user_id
                              WHERE h.news_event_id = :e ORDER BY h.at, h.id""", {"e": event_id}),
        "evaluations": rows(s, """SELECT id, decision, priority, output, created_at FROM eic_evaluation
                                  WHERE news_event_id = :e ORDER BY created_at DESC""", {"e": event_id}),
        "assignments": rows(s, """SELECT a.*, acc.handle, acc.language FROM assignment a
                                  JOIN editorial_account acc ON acc.id = a.account_id WHERE a.news_event_id = :e
                                  ORDER BY a.id""", {"e": event_id}),
        "articles": rows(s, """SELECT ar.id, ar.account_id, acc.handle, acc.platform, ar.language, ar.status,
                                      ar.current_version_id, ar.deleted_at, asg.source, v.headline, v.guard_status,
                                      v.fact_packet_version, v.origin, v.model,
                                      (SELECT count(*) FROM article_version x WHERE x.article_id = ar.id) AS versions
                               FROM article ar JOIN editorial_account acc ON acc.id = ar.account_id
                               LEFT JOIN assignment asg ON asg.id = ar.assignment_id
                               LEFT JOIN article_version v ON v.id = ar.current_version_id
                               WHERE ar.news_event_id = :e ORDER BY ar.deleted_at NULLS FIRST, ar.id""", {"e": event_id}),
        "pending_drafts": s.execute(text("""SELECT count(*) FROM job j JOIN assignment a ON a.id = (j.payload->>'assignment_id')::bigint
                                            WHERE j.kind = 'draft_generate' AND j.status IN ('queued', 'running')
                                              AND a.news_event_id = :e"""), {"e": event_id}).scalar(),
        "story": one(s, "SELECT * FROM news_story WHERE id = :s", {"s": ev["story_id"]}) if ev["story_id"] else None,
        "corrections": rows(s, "SELECT * FROM correction_record WHERE news_event_id = :e ORDER BY id DESC",
                            {"e": event_id}),
    }


@router.get("/events/{event_id}/packets/{version}")
def packet_version(event_id: int, version: int, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(active_user)):
    p = one(s, "SELECT * FROM fact_packet WHERE news_event_id = :e AND version = :v", {"e": event_id, "v": version})
    if p is None:
        raise HTTPException(404, "no such packet version")
    return p


class Reason(BaseModel):
    reason: str = Field("", max_length=1000)


@router.post("/events/{event_id}/evaluate")
def queue_evaluation(event_id: int, s: Session = Depends(get_session),
                     user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    ev = one(s, "SELECT state, gate_status, current_fact_version FROM news_event WHERE id = :e", {"e": event_id})
    if ev is None:
        raise HTTPException(404, "event not found")
    if ev["state"] not in ("candidate", "evaluated") or ev["gate_status"] != "pass":
        raise HTTPException(409, f"event is {ev['state']} with gate {ev['gate_status']}")
    jid = enqueue(s, "eic_evaluate", {"event_id": event_id}, dedupe_key=f"eic:{event_id}:{ev['current_fact_version']}:manual",
                  priority=40)
    audit.record(s, "event.evaluate_requested", "news_event", event_id, user_id=user.id)
    s.commit()
    return {"job_id": jid}


@router.post("/events/{event_id}/refresh")
def queue_refresh(event_id: int, s: Session = Depends(get_session),
                  user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    jid = enqueue(s, "build_facts", {"event_id": event_id}, dedupe_key=f"facts:{event_id}", priority=30)
    s.commit()
    return {"job_id": jid}


class StateChange(BaseModel):
    to: Literal["declined", "archived", "candidate", "dismissed"]
    reason: str = Field("", max_length=1000)


@router.post("/events/{event_id}/state")
def change_state(event_id: int, body: StateChange, s: Session = Depends(get_session),
                 user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    try:
        prev = transition(s, event_id, body.to, actor_kind="user", user_id=user.id, reason=body.reason)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.to in ("declined", "archived"):
        s.execute(text("""UPDATE assignment SET status = 'cancelled' WHERE news_event_id = :e
                          AND status IN ('proposed', 'accepted')"""), {"e": event_id})
    audit.record(s, "event.state", "news_event", event_id, user_id=user.id, before={"state": prev},
                 after={"state": body.to}, reason=body.reason)
    s.commit()
    return {"ok": True, "from": prev}


class NewAssignment(BaseModel):
    account_id: int
    angle: str = Field("", max_length=300)
    reason: str = Field("", max_length=500)
    format: Literal["single_post", "thread", "card_post"] = "single_post"


@router.post("/events/{event_id}/assignments")
def add_assignment(event_id: int, body: NewAssignment, s: Session = Depends(get_session),
                   user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    check_account(s, user, body.account_id, "draft")
    ev = one(s, "SELECT state, gate_status FROM news_event WHERE id = :e", {"e": event_id})
    if ev is None or ev["state"] not in ("candidate", "evaluated", "assigned", "covered"):
        raise HTTPException(409, "event is not open for assignments")
    aid = assignment.create(s, event_id, body.account_id, source="editor", angle=body.angle, reason=body.reason,
                            fmt=body.format, status="proposed", user_id=user.id)
    if aid is None:
        raise HTTPException(409, "this account already has an active assignment for the event")
    audit.record(s, "assignment.create", "assignment", aid, user_id=user.id, account_id=body.account_id)
    s.commit()
    return {"assignment_id": aid}


def _assignment_account(s: Session, assignment_id: int) -> int:
    acc = s.execute(text("SELECT account_id FROM assignment WHERE id = :i"), {"i": assignment_id}).scalar()
    if acc is None:
        raise HTTPException(404, "assignment not found")
    return acc


@router.post("/assignments/{assignment_id}/accept")
def accept_assignment(assignment_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    check_account(s, user, _assignment_account(s, assignment_id), "draft")
    art = assignment.accept(s, assignment_id, user.id)
    s.commit()
    return {"article_id": art}


@router.post("/assignments/{assignment_id}/reject")
def reject_assignment(assignment_id: int, body: Reason, s: Session = Depends(get_session),
                      user: CurrentUser = Depends(active_user)):
    check_account(s, user, _assignment_account(s, assignment_id), "draft")
    assignment.reject(s, assignment_id, user.id, body.reason)
    s.commit()
    return {"ok": True}


@router.post("/assignments/{assignment_id}/draft")
def queue_draft(assignment_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    check_account(s, user, _assignment_account(s, assignment_id), "draft")
    st = s.execute(text("SELECT status FROM assignment WHERE id = :i"), {"i": assignment_id}).scalar()
    if st not in ("accepted", "drafting"):
        raise HTTPException(409, "accept the assignment first")
    jid = enqueue(s, "draft_generate", {"assignment_id": assignment_id, "user_id": user.id},
                  dedupe_key=f"draft:{assignment_id}", priority=50)
    s.commit()
    return {"job_id": jid}


@router.get("/stories")
def list_stories(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                 limit: int = Query(100, le=500)):
    return rows(s, """SELECT st.*, t.trader_key, t.display_name, t.public_label,
                             (SELECT count(*) FROM story_event se WHERE se.story_id = st.id) AS chapters,
                             (SELECT count(*) FROM story_event se JOIN article a ON a.news_event_id = se.news_event_id
                              JOIN publication_record pr ON pr.article_id = a.id
                              WHERE se.story_id = st.id AND pr.status = 'published') AS publications
                      FROM news_story st LEFT JOIN trader t ON t.id = st.trader_id
                      ORDER BY coalesce(st.last_event_at, st.opened_at) DESC LIMIT :n""", {"n": limit})


@router.get("/stories/{story_id}")
def story_detail(story_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    st = one(s, """SELECT st.*, t.trader_key, t.display_name, t.public_label FROM news_story st
                   LEFT JOIN trader t ON t.id = st.trader_id WHERE st.id = :s""", {"s": story_id})
    if st is None:
        raise HTTPException(404, "story not found")
    return {"story": st, "timeline": rows(s, """
        SELECT se.seq, se.role, se.pinned_fact_packet_id, e.id AS event_id, e.event_class, e.coin, e.direction,
               e.state, e.score, e.liquidation_status, e.detected_at, fp.version AS pinned_version,
               (SELECT json_agg(json_build_object('handle', acc.handle, 'url', pr.x_url, 'at', pr.published_at,
                                                  'status', pr.status))
                FROM article a JOIN publication_record pr ON pr.article_id = a.id
                JOIN editorial_account acc ON acc.id = pr.account_id WHERE a.news_event_id = e.id) AS publications
        FROM story_event se JOIN news_event e ON e.id = se.news_event_id
        LEFT JOIN fact_packet fp ON fp.id = se.pinned_fact_packet_id
        WHERE se.story_id = :s ORDER BY se.seq""", {"s": story_id})}


@router.get("/corrections")
def list_corrections(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user),
                     status: str | None = "open"):
    return rows(s, f"""SELECT c.*, e.event_class, e.coin FROM correction_record c JOIN news_event e ON e.id = c.news_event_id
                       {"WHERE c.status = :st" if status else ""} ORDER BY c.detected_at DESC LIMIT 200""",
                {"st": status})


class CorrectionUpdate(BaseModel):
    status: Literal["acknowledged", "corrected", "no_action"]
    note: str = Field("", max_length=2000)


@router.post("/corrections/{cid}")
def handle_correction(cid: int, body: CorrectionUpdate, s: Session = Depends(get_session),
                      user: CurrentUser = Depends(require_roles(*EVENT_DESK_ROLES))):
    n = s.execute(text("""UPDATE correction_record SET status = :st, note = :n, handled_by = :u, handled_at = :t
                          WHERE id = :i"""), {"st": body.status, "n": body.note, "u": user.id, "t": now(), "i": cid}
                  ).rowcount
    if not n:
        raise HTTPException(404, "correction not found")
    audit.record(s, "correction.handle", "correction", cid, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"ok": True}
