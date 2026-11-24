"""Drafts expire with their news: a whale alert is worth posting for a few hours after the move was seen, a column for
a day. Expired drafts leave the ready list and the publishing API; a rewrite from fresh facts can bring one back."""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import audit, settings_store

LIVE = ("drafting", "draft_ready", "in_review", "changes_requested", "approved", "stale")


def expires_at(s: Session, event_id: int) -> datetime | None:
    """When the last observation of this event plus its class's shelf life runs out."""
    ev = s.execute(text("""
        SELECT ne.event_class, ne.column_key, coalesce(se.observed_at, ne.detected_at) AS seen
        FROM news_event ne LEFT JOIN source_event se ON se.id = ne.last_source_event_id WHERE ne.id = :e"""),
        {"e": event_id}).mappings().first()
    if ev is None:
        return None
    rule = settings_store.get(s, "expiry")
    hours = rule["column_hours"] if ev["column_key"] else rule["hours"].get(ev["event_class"], rule["default_hours"])
    return ev["seen"] + timedelta(hours=hours)


def expire_due(sf: sessionmaker, now: datetime) -> int:
    with sf() as s:
        gone = s.execute(text(f"""
            UPDATE article SET status = 'expired', updated_at = now(), claimed_by_token = NULL, claimed_at = NULL
            WHERE expires_at < :n AND status IN ({", ".join(f"'{x}'" for x in LIVE)}) AND deleted_at IS NULL
            RETURNING id, account_id"""), {"n": now}).mappings().all()
        for a in gone:
            s.execute(text("""UPDATE publication_plan SET status = 'cancelled', note = 'expired'
                              WHERE article_id = :a AND status IN ('planned', 'claimed')"""), {"a": a["id"]})
            audit.record(s, "article.expired", "article", a["id"], actor_kind="system", account_id=a["account_id"])
        s.commit()
    return len(gone)
