"""When an event's facts change materially: unpublished drafts written from the old packet become stale, and
published posts that cited a changed fact are listed in a correction record for an editor to handle."""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

FINAL = ("published", "retracted", "killed")


def on_new_packet(s: Session, event_id: int, old_packet_id: int | None, new_packet_id: int,
                  changed: list[str]) -> int | None:
    if old_packet_id is None:
        return None
    arts = s.execute(text("""
        SELECT a.id, a.status, v.fact_packet_id, v.facts_used FROM article a
        JOIN article_version v ON v.id = a.current_version_id WHERE a.news_event_id = :e"""), {"e": event_id}
    ).mappings().all()
    event_level = any(c.startswith("<") for c in changed)
    stale = []
    for a in arts:
        if a["status"] in FINAL or a["fact_packet_id"] == new_packet_id:
            continue
        s.execute(text("""UPDATE article SET status = 'stale', updated_at = now(), stale_reason = :r WHERE id = :i"""),
                  {"i": a["id"], "r": f"facts changed: {', '.join(changed[:8])}"})
        stale.append({"article_id": a["id"], "cited_changed": sorted(set(a["facts_used"]) & set(changed))})
    pubs = s.execute(text("""
        SELECT pr.id, pr.account_id, v.facts_used FROM publication_record pr
        JOIN article_version v ON v.id = pr.article_version_id JOIN article a ON a.id = pr.article_id
        WHERE a.news_event_id = :e AND pr.status = 'published'"""), {"e": event_id}).mappings().all()
    affected_pubs = [{"publication_id": p["id"], "account_id": p["account_id"],
                      "cited_changed": sorted(set(p["facts_used"]) & set(changed))}
                     for p in pubs if event_level or set(p["facts_used"]) & set(changed)]
    if not stale and not affected_pubs:
        return None
    material = bool(affected_pubs) or event_level or any(a["cited_changed"] for a in stale)
    return s.execute(text("""
        INSERT INTO correction_record (news_event_id, old_fact_packet_id, new_fact_packet_id, material, changed_facts,
                                       affected)
        VALUES (:e, :o, :n, :m, CAST(:c AS jsonb), CAST(:a AS jsonb)) RETURNING id"""),
        {"e": event_id, "o": old_packet_id, "n": new_packet_id, "m": material, "c": json.dumps(changed),
         "a": json.dumps({"articles": stale, "publications": affected_pubs})}).scalar_one()
