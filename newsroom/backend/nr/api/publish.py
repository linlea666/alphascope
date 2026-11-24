"""The publishing center: every post worth sending in one place, by language, account and style, with what the AI desk
reviewer said, how long it stays timely, and the actions that get it out (copy, open X, "I posted it")."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now, one, rows
from nr.auth.deps import active_user, check_account, visible_accounts
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.jobs.queue import enqueue

router = APIRouter(prefix="/api", tags=["publish"])
Tab = Literal["ready", "todo", "published", "expired", "trash"]
TABS = {
    "ready": "a.status = 'approved' AND a.deleted_at IS NULL",
    "todo": "a.status IN ('draft_ready', 'changes_requested', 'in_review', 'stale') AND a.deleted_at IS NULL",
    "published": "a.status = 'published'",
    "expired": "a.status = 'expired' AND a.deleted_at IS NULL",
    "trash": "a.deleted_at IS NOT NULL",
}
CARD_SQL = """
    SELECT a.id, a.status, a.language, a.expires_at, a.updated_at, a.deleted_at IS NOT NULL AS deleted,
           acc.id AS account_id, acc.handle, acc.display_name, acc.platform, asg.source,
           ne.id AS event_id, ne.event_class, ne.coin, ne.direction,
           v.id AS version_id, v.headline, v.body, v.thread, v.reply_text, v.style_key, v.model, v.agent_key,
           v.guard_status, (v.guard_report->>'x_length')::int AS x_length, (v.guard_report->>'length_limit')::int AS length_limit,
           st.name AS style_name,
           (SELECT m.id FROM media_asset m WHERE m.fact_packet_id = v.fact_packet_id AND m.template_key = v.card_template
              AND m.language = a.language AND m.kind = 'card_png' ORDER BY m.id DESC LIMIT 1) AS card_asset_id,
           (SELECT to_jsonb(r) FROM (SELECT verdict, scores, issues, summary FROM article_review
              WHERE article_version_id = v.id ORDER BY id DESC LIMIT 1) r) AS review,
           (SELECT to_jsonb(p) FROM (SELECT x_url, reply_url, channel, published_at FROM publication_record
              WHERE article_id = a.id AND status = 'published' ORDER BY id DESC LIMIT 1) p) AS publication
    FROM article a JOIN editorial_account acc ON acc.id = a.account_id JOIN news_event ne ON ne.id = a.news_event_id
    LEFT JOIN assignment asg ON asg.id = a.assignment_id JOIN article_version v ON v.id = a.current_version_id
    LEFT JOIN LATERAL (SELECT name FROM editorial_style WHERE key = v.style_key ORDER BY version DESC LIMIT 1) st ON true"""


@router.get("/publish")
def publish_center(tab: Tab = "ready", language: Literal["zh", "en"] | None = None, account_id: int | None = None,
                   style: str | None = Query(None, max_length=64), event_class: str | None = Query(None, max_length=32),
                   s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    vis = visible_accounts(s, user)
    where, p = [TABS[tab]], {"vis": vis, "now": now()}
    for col, val in (("a.language", language), ("a.account_id", account_id), ("v.style_key", style),
                     ("ne.event_class", event_class)):
        if val is not None:
            where.append(f"{col} = :{col.split('.')[1]}")
            p[col.split(".")[1]] = val
    if vis is not None:
        where.append("a.account_id = ANY(:vis)")
    order = "a.expires_at NULLS LAST" if tab in ("ready", "todo") else "a.updated_at DESC"
    cards = rows(s, f"{CARD_SQL} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200", p)
    counts = dict(s.execute(text(f"""
        SELECT t, count(*) FROM (SELECT CASE WHEN {TABS['ready']} THEN 'ready' WHEN {TABS['todo']} THEN 'todo'
                                             WHEN {TABS['published']} THEN 'published' WHEN {TABS['expired']} THEN 'expired'
                                             WHEN {TABS['trash']} THEN 'trash' END AS t
                                 FROM article a {"WHERE a.account_id = ANY(:vis)" if vis is not None else ""}) x
        WHERE t IS NOT NULL GROUP BY t"""), {"vis": vis}).all())
    today = one(s, """SELECT count(*) FILTER (WHERE status = 'published' AND published_at > now() - interval '24 hours') AS published_24h
                      FROM publication_record""")
    styles = rows(s, "SELECT DISTINCT ON (key) key, name, language FROM editorial_style ORDER BY key, version DESC")
    return {"tab": tab, "cards": cards, "counts": counts, "now": p["now"], "today": today, "styles": styles}


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
    enqueue(s, "build_facts", {"event_id": a["news_event_id"]}, dedupe_key=f"facts:{a['news_event_id']}", priority=20, now=t)
    job = enqueue(s, "draft_generate", {"assignment_id": a["assignment_id"], "user_id": user.id, "mode": "draft"},
                  dedupe_key=f"rewrite:{article_id}", priority=30, now=t + timedelta(seconds=45))
    audit.record(s, "article.rewrite", "article", article_id, user_id=user.id, account_id=a["account_id"])
    s.commit()
    return {"job": job}
