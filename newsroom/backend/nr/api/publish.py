"""The publishing center: every post worth sending in one place, by language, account and style, with what the AI desk
reviewer said, how long it stays timely, and the actions that get it out (copy, open X, "I posted it")."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now, one, rows
from nr.articles import versions
from nr.auth.deps import active_user, check_account, visible_accounts
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.jobs.queue import enqueue
from nr.publish import service as pub
from nr.publish.expiry import expires_at

router = APIRouter(prefix="/api", tags=["publish"])
Tab = Literal["ready", "review", "attention", "writing", "published", "trash"]
LATEST_REVIEW = """(SELECT r.verdict FROM article_review r WHERE r.article_version_id = a.current_version_id
                    ORDER BY r.id DESC LIMIT 1)"""
CUR_GUARD = "(SELECT guard_status FROM article_version WHERE id = a.current_version_id)"
# One plain state per article (docs/18). "ready" is exactly what the RPA / extension API hands out
# (nr/api/outbound.py READY): approved, in time, account active.
TABS = {
    "ready": "a.status = 'approved' AND a.deleted_at IS NULL AND (a.expires_at IS NULL OR a.expires_at > now())",
    "review": f"""a.status IN ('draft_ready', 'in_review', 'changes_requested') AND a.deleted_at IS NULL
                  AND {CUR_GUARD} IS DISTINCT FROM 'fail' AND {LATEST_REVIEW} IS DISTINCT FROM 'reject'""",
    "attention": f"""a.deleted_at IS NULL AND (a.status = 'stale'
                     OR (a.status IN ('draft_ready', 'in_review', 'changes_requested')
                         AND ({CUR_GUARD} = 'fail' OR {LATEST_REVIEW} = 'reject')))""",
    "writing": "a.status = 'drafting' AND a.deleted_at IS NULL",
    "published": "a.status = 'published'",
    "trash": "(a.deleted_at IS NOT NULL OR a.status IN ('expired', 'killed', 'retracted'))",
}
WHY = f"""CASE WHEN a.status = 'stale' THEN 'stale' WHEN {CUR_GUARD} = 'fail' THEN 'guard'
               WHEN {LATEST_REVIEW} = 'reject' THEN 'rejected' END"""
CARD_SQL = """
    SELECT a.id, a.status, a.language, a.expires_at, a.updated_at, a.deleted_at IS NOT NULL AS deleted,
           acc.id AS account_id, acc.handle, acc.display_name, acc.platform, asg.source,
           ne.id AS event_id, ne.event_class, ne.coin, ne.direction, ne.column_key, ne.venue,
           v.id AS version_id, v.headline, v.body, v.thread, v.reply_text, v.style_key, v.model, v.agent_key, v.facts_used,
           v.guard_status, (v.guard_report->>'x_length')::int AS x_length, (v.guard_report->>'length_limit')::int AS length_limit,
           st.name AS style_name, (SELECT z.name FROM editorial_style z WHERE z.key = 'zh_' || substring(v.style_key from 4) ORDER BY z.version DESC LIMIT 1) AS style_zh, (SELECT count(*) FROM article_version x WHERE x.article_id = a.id) AS n_versions,
           (SELECT t.name FROM api_token t WHERE t.id = a.claimed_by_token) AS claimed_by,
           a.claimed_at, {why} AS why,
           (SELECT m.id FROM media_asset m JOIN fact_packet mp ON mp.id = m.fact_packet_id
              WHERE mp.news_event_id = a.news_event_id AND m.template_key = v.card_template AND m.language = a.language
                AND m.kind = 'card_png' ORDER BY (m.fact_packet_id = v.fact_packet_id) DESC, m.id DESC LIMIT 1) AS card_asset_id,
           (SELECT to_jsonb(r) FROM (SELECT verdict, scores, issues, summary FROM article_review
              WHERE article_version_id = v.id ORDER BY id DESC LIMIT 1) r) AS review,
           (SELECT to_jsonb(p) FROM (SELECT x_url, reply_url, channel, published_at, matches_version,
                     guard_report->>'status' AS guard_status,
                     (SELECT jsonb_agg(f->>'code') FROM jsonb_array_elements(guard_report->'findings') f
                        WHERE f->>'severity' = 'warn') AS warnings
              FROM publication_record WHERE article_id = a.id AND status = 'published' ORDER BY id DESC LIMIT 1) p)
             AS publication,
           (SELECT jsonb_build_object('id', p.id, 'planned_at', p.planned_at, 'status', p.status) FROM publication_plan p
              WHERE p.article_id = a.id AND p.status IN ('planned', 'claimed') ORDER BY p.id DESC LIMIT 1) AS plan,
           (SELECT pr.id FROM publication_record pr WHERE pr.article_id = a.id AND pr.status = 'published'
              AND pr.published_at < now() - interval '24 hours'
              AND NOT EXISTS (SELECT 1 FROM performance_snapshot ps WHERE ps.publication_id = pr.id)
              ORDER BY pr.id DESC LIMIT 1) AS metrics_due
    FROM article a JOIN editorial_account acc ON acc.id = a.account_id JOIN news_event ne ON ne.id = a.news_event_id
    LEFT JOIN assignment asg ON asg.id = a.assignment_id JOIN article_version v ON v.id = a.current_version_id
    LEFT JOIN LATERAL (SELECT name FROM editorial_style WHERE key = v.style_key ORDER BY version DESC LIMIT 1) st ON true"""


CARD_SQL = CARD_SQL.replace("{why}", WHY)


@router.get("/publish")
def publish_center(tab: Tab = "ready", language: Literal["zh", "en"] | None = None, account_id: int | None = None,
                   style: str | None = Query(None, max_length=64), event_class: str | None = Query(None, max_length=32),
                   venue: Literal["hyperliquid", "okx", "binance", "multi"] | None = None,
                   agent: str | None = Query(None, max_length=48),
                   pair: bool = False, within: int | None = Query(None, ge=5, le=1440),
                   s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """With `pair`, the other-language posts on the same events come along (from any tab but the trash), so the
    Chinese and English versions can be read side by side."""
    vis = visible_accounts(s, user)
    where, p = [TABS[tab]], {"vis": vis, "now": now()}
    for col, val in (("a.language", language), ("a.account_id", account_id), ("v.style_key", style),
                     ("ne.event_class", event_class), ("ne.venue", venue), ("v.agent_key", agent)):
        if val is not None:
            where.append(f"{col} = :{col.split('.')[1]}")
            p[col.split(".")[1]] = val
    if vis is not None:
        where.append("a.account_id = ANY(:vis)")
    if within is not None:  # minutes of shelf life left
        where.append("a.expires_at <= :until")
        p["until"] = p["now"] + timedelta(minutes=within)
    order = "a.expires_at NULLS LAST" if tab in ("ready", "review", "attention") else "a.updated_at DESC"
    cards = rows(s, f"{CARD_SQL} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200", p)
    counts = {k: s.execute(text(f"""SELECT count(*) FROM article a WHERE {cond}
                                    {"AND a.account_id = ANY(:vis)" if vis is not None else ""}"""), {"vis": vis}).scalar()
              for k, cond in TABS.items()}
    today = one(s, f"""SELECT count(*) AS published_today FROM publication_record
                       WHERE status = 'published' AND published_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')
                                                                      AT TIME ZONE 'Asia/Shanghai'
                       {"AND account_id = ANY(:vis)" if vis is not None else ""}""", {"vis": vis})
    agents = rows(s, """SELECT DISTINCT v.agent_key FROM article a JOIN article_version v ON v.id = a.current_version_id
                        WHERE a.updated_at > now() - interval '14 days' AND v.agent_key IS NOT NULL ORDER BY 1""")
    styles = rows(s, "SELECT DISTINCT ON (key) key, name, language FROM editorial_style ORDER BY key, version DESC")
    siblings = rows(s, f"""{CARD_SQL} WHERE a.news_event_id = ANY(:e) AND a.id <> ALL(:ids) AND a.deleted_at IS NULL
                           AND a.status NOT IN ('killed', 'retracted') {"AND a.account_id = ANY(:vis)" if vis is not None else ""}
                           ORDER BY a.id DESC""",
                    {"e": list({c["event_id"] for c in cards}), "ids": [c["id"] for c in cards], "vis": vis}) \
        if pair and cards else []
    return {"tab": tab, "cards": cards, "siblings": siblings, "counts": counts, "now": p["now"], "today": today,
            "styles": styles, "agents": [r["agent_key"] for r in agents],
            "robots": rows(s, "SELECT name, channel, last_used_at FROM api_token WHERE active ORDER BY last_used_at DESC NULLS LAST")}


@router.post("/articles/{article_id}/revise")
def revise_by_review(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """One more pass by the desk editor with the latest AI review's notes (the automatic pass is once per draft)."""
    a = one(s, """SELECT a.account_id, a.assignment_id, a.status, a.current_version_id FROM article a WHERE a.id = :a""",
            {"a": article_id})
    if a is None:
        raise HTTPException(404, "article not found")
    check_account(s, user, a["account_id"], "draft")
    if a["status"] not in ("draft_ready", "changes_requested", "in_review", "approved", "stale"):
        raise HTTPException(409, f"article is {a['status']}")
    r = one(s, """SELECT r.summary, r.revision_note, r.issues FROM article_review r
                  WHERE r.article_version_id = :v ORDER BY r.id DESC LIMIT 1""", {"v": a["current_version_id"]})
    if r is None:
        raise HTTPException(409, "the current version has no AI review yet")
    job = enqueue(s, "draft_generate", {"assignment_id": a["assignment_id"], "user_id": user.id, "mode": "polish",
                                        "review_note": (r["revision_note"] or r["summary"])[:1500],
                                        "review_issues": r["issues"]},
                  dedupe_key=f"revise:{a['current_version_id']}:manual", priority=30, now=now())
    if a["status"] == "approved":  # a revised text needs approving again
        s.execute(text("UPDATE article SET status = 'draft_ready', updated_at = now() WHERE id = :a"), {"a": article_id})
    audit.record(s, "article.revise_by_review", "article", article_id, user_id=user.id, account_id=a["account_id"])
    s.commit()
    return {"job": job}


class CheckTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/articles/{article_id}/check-text")
def check_text(article_id: int, body: CheckTextIn, s: Session = Depends(get_session),
               user: CurrentUser = Depends(active_user)):
    """Before "I posted it" with an edited text: what the claims guard says about the text that actually went out."""
    art = versions.load(s, article_id)
    check_account(s, user, art["account_id"], "publish")
    v = versions.current_version(s, article_id)
    _, report, matches = pub.check_posted(s, art, v, body.text)
    return {"matches_version": matches, "blocked": bool(pub.hard_findings(report)), "status": report["status"],
            "findings": [{"code": f["code"], "severity": f["severity"], "text": f.get("text")}
                         for f in report["findings"] if f["severity"] in ("fail", "warn")]}


@router.post("/articles/{article_id}/rewrite")
def rewrite_from_fresh_facts(article_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """Refresh the event's facts, then draft again: how an expired or stale post comes back if the news moved on."""
    a = one(s, """SELECT ar.account_id, ar.news_event_id, ar.assignment_id, ar.status FROM article ar WHERE ar.id = :a""",
            {"a": article_id})
    if a is None:
        raise HTTPException(404, "article not found")
    check_account(s, user, a["account_id"], "draft")
    if a["status"] in ("published", "retracted", "killed"):
        raise HTTPException(409, f"article is {a['status']}")
    t = now()
    if (exp := expires_at(s, a["news_event_id"])) and exp < t:
        raise HTTPException(409, "nothing new since this went out of date: rewriting works again once the position moves")
    enqueue(s, "build_facts", {"event_id": a["news_event_id"]}, dedupe_key=f"facts:{a['news_event_id']}", priority=20, now=t)
    job = enqueue(s, "draft_generate", {"assignment_id": a["assignment_id"], "user_id": user.id, "mode": "draft",
                                        "sequel": True},
                  dedupe_key=f"rewrite:{article_id}", priority=30, now=t + timedelta(seconds=45))
    audit.record(s, "article.rewrite", "article", article_id, user_id=user.id, account_id=a["account_id"])
    s.commit()
    return {"job": job}
