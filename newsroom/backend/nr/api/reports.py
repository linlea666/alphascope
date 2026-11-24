"""The weekly quality report, for the desk only: what got written, what got out, what the AI reviewer thought of it
and, where posts have metrics, how readers responded. Computed on request from the records; nothing is stored."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from nr.api.common import now, one, rows
from nr.auth.deps import active_user, visible_accounts
from nr.auth.sessions import CurrentUser
from nr.db import get_session

router = APIRouter(prefix="/api", tags=["reports"])

# one row per article written in the window: its current version, the latest review of that version, and its
# publication with the latest metrics
BASE = """
    WITH w AS (
      SELECT a.id, a.status, a.language, acc.handle, coalesce(v.style_key, '-') AS style, coalesce(v.agent_key, '-') AS agent,
             v.guard_status, r.verdict, (r.scores->>'publishable')::int AS publishable, (r.scores->>'ai_tone')::int AS ai_tone,
             (r.scores->>'native')::int AS native, (r.scores->>'hook')::int AS hook, pr.id AS pub_id,
             ps.impressions, ps.likes, ps.reposts, ps.replies
      FROM article a JOIN editorial_account acc ON acc.id = a.account_id
      JOIN article_version v ON v.id = a.current_version_id
      LEFT JOIN LATERAL (SELECT verdict, scores FROM article_review WHERE article_version_id = v.id
                         ORDER BY id DESC LIMIT 1) r ON true
      LEFT JOIN LATERAL (SELECT id FROM publication_record WHERE article_id = a.id AND status = 'published'
                         ORDER BY id DESC LIMIT 1) pr ON true
      LEFT JOIN LATERAL (SELECT impressions, likes, reposts, replies FROM performance_snapshot
                         WHERE publication_id = pr.id ORDER BY captured_at DESC LIMIT 1) ps ON true
      WHERE a.created_at > :since AND a.deleted_at IS NULL {vis})"""
AGG = """
    count(*) AS written, count(pub_id) AS published, count(*) FILTER (WHERE status = 'expired') AS expired,
    count(*) FILTER (WHERE guard_status = 'fail') AS guard_failed, count(verdict) AS reviewed,
    count(*) FILTER (WHERE verdict = 'approve') AS review_approved,
    round(avg(publishable), 2) AS publishable, round(avg(ai_tone), 2) AS ai_tone, round(avg(native), 2) AS native,
    round(avg(hook), 2) AS hook, count(likes) AS with_metrics, round(avg(impressions)) AS impressions,
    round(avg(likes), 1) AS likes, round(avg(reposts), 1) AS reposts, round(avg(replies), 1) AS replies"""


@router.get("/reports/weekly")
def weekly(days: int = Query(7, ge=1, le=90), s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    vis = visible_accounts(s, user)
    t = now()
    p = {"since": t - timedelta(days=days), "vis": vis}
    base = BASE.format(vis="" if vis is None else "AND a.account_id = ANY(:vis)")
    by = lambda col: rows(s, f"{base} SELECT {col} AS key, {AGG} FROM w GROUP BY 1 ORDER BY written DESC", p)  # noqa: E731
    return {
        "days": days, "as_of": t,
        "total": one(s, f"{base} SELECT {AGG} FROM w", p),
        "by_style": by("style"), "by_agent": by("agent"), "by_account": by("handle"),
        # does a better review score mean a better-received post? (only posts with metrics count)
        "by_publishable": rows(s, f"""{base} SELECT publishable AS key, {AGG} FROM w WHERE publishable IS NOT NULL
                                      GROUP BY 1 ORDER BY 1""", p),
        "ai_calls": one(s, """SELECT count(*) AS calls, count(*) FILTER (WHERE status <> 'ok') AS failed,
                                     coalesce(sum(cost_usd), 0) AS cost_usd
                              FROM ai_call_log WHERE created_at > :since""", p),
    }
