"""Public, read-only pages: only what has been published, plus the corrections and the full tracking ledger (misses
included). No drafts, no unpublished facts, no internal notes. Served without a session."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from nr.api.common import one, rows
from nr.db import get_session

router = APIRouter(prefix="/api/public", tags=["public"])
KEY_FACTS = ("position.notional_usd", "change.notional_usd", "position.leverage", "position.unrealized_pnl_usd",
             "close.pnl_estimate_usd", "liquidation.closed_pnl_usd", "change.window", "episode.first_seen_at")
PUBLISHED = "EXISTS (SELECT 1 FROM article a JOIN publication_record pr ON pr.article_id = a.id " \
            "WHERE a.news_event_id = e.id AND pr.status = 'published')"


def public_name(trader_key: str | None, label: str | None) -> str | None:
    if label:
        return label
    if not trader_key:
        return None
    return f"OKX·{trader_key[-6:]}" if trader_key.startswith("okx:") else f"{trader_key[:6]}…{trader_key[-4:]}"


@router.get("/stories")
def public_stories(s: Session = Depends(get_session)):
    out = rows(s, f"""
        SELECT st.id, st.status, t.trader_key, t.public_label, count(DISTINCT e.id) AS chapters,
               max(pr.published_at) AS last_published
        FROM news_story st JOIN story_event se ON se.story_id = st.id JOIN news_event e ON e.id = se.news_event_id
        JOIN article a ON a.news_event_id = e.id JOIN publication_record pr ON pr.article_id = a.id AND pr.status = 'published'
        LEFT JOIN trader t ON t.id = st.trader_id
        GROUP BY st.id, st.status, t.trader_key, t.public_label ORDER BY max(pr.published_at) DESC LIMIT 200""")
    for r in out:
        r["name"] = public_name(r.pop("trader_key"), r.pop("public_label"))
    return out


@router.get("/stories/{story_id}")
def public_story(story_id: int, s: Session = Depends(get_session)):
    st = one(s, """SELECT st.id, st.status, t.trader_key, t.public_label FROM news_story st
                   LEFT JOIN trader t ON t.id = st.trader_id WHERE st.id = :s""", {"s": story_id})
    if st is None:
        raise HTTPException(404, "story not found")
    chapters = rows(s, f"""
        SELECT se.seq, se.role, e.id AS event_id, e.event_class, e.coin, e.direction, e.liquidation_status,
               fp.version AS fact_version, fp.content->'facts' AS facts, fp.content->'constraints' AS constraints,
               ep.status AS position_now
        FROM story_event se JOIN news_event e ON e.id = se.news_event_id
        LEFT JOIN fact_packet fp ON fp.id = se.pinned_fact_packet_id LEFT JOIN position_episode ep ON ep.id = e.episode_id
        WHERE se.story_id = :s AND {PUBLISHED} ORDER BY se.seq""", {"s": story_id})
    if not chapters:
        raise HTTPException(404, "nothing published in this story")
    for ch in chapters:
        ch["facts"] = [{"id": f["id"], "display": f["display"], "label": f.get("label")}
                       for f in (ch["facts"] or []) if f["id"] in KEY_FACTS]
        ch["posts"] = rows(s, """
            SELECT acc.handle, pr.x_url, pr.published_at, pr.final_text, pr.status, pr.retract_reason
            FROM publication_record pr JOIN article a ON a.id = pr.article_id JOIN editorial_account acc ON acc.id = pr.account_id
            WHERE a.news_event_id = :e ORDER BY pr.published_at""", {"e": ch["event_id"]})
    return {"story": {"id": st["id"], "status": st["status"], "name": public_name(st["trader_key"], st["public_label"])},
            "chapters": chapters}


@router.get("/ledger")
def tracking_ledger(s: Session = Depends(get_session)):
    """Every position we reported on and where it stands now, wins and misses alike."""
    out = rows(s, f"""
        SELECT e.id, e.event_class, e.coin, e.direction, e.story_id, min(pr.published_at) AS first_published,
               ep.status AS position_now, ep.end_to AS closed_by, t.trader_key, t.public_label
        FROM news_event e JOIN article a ON a.news_event_id = e.id
        JOIN publication_record pr ON pr.article_id = a.id AND pr.status = 'published'
        LEFT JOIN position_episode ep ON ep.id = e.episode_id LEFT JOIN trader t ON t.id = e.trader_id
        WHERE e.column_key IS NULL
        GROUP BY e.id, ep.status, ep.end_to, t.trader_key, t.public_label ORDER BY min(pr.published_at) DESC LIMIT 500""")
    for r in out:
        r["name"] = public_name(r.pop("trader_key"), r.pop("public_label"))
    return out


@router.get("/corrections")
def public_corrections(s: Session = Depends(get_session)):
    return {"corrections": rows(s, """
        SELECT c.id, c.detected_at, c.status, c.handled_at, c.note, c.changed_facts, e.event_class, e.coin
        FROM correction_record c JOIN news_event e ON e.id = c.news_event_id
        WHERE c.material AND c.affected->'publications' <> '[]'::jsonb ORDER BY c.detected_at DESC LIMIT 200"""),
            "retractions": rows(s, """
        SELECT pr.id, acc.handle, pr.x_url, pr.published_at, pr.retracted_at, pr.retract_reason
        FROM publication_record pr JOIN editorial_account acc ON acc.id = pr.account_id
        WHERE pr.status = 'retracted' ORDER BY pr.retracted_at DESC LIMIT 200""")}
