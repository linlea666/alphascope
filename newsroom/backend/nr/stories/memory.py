"""Story memory for writers: what this story already published, the questions it left open, and which facts changed
since the last post. Only published text counts as memory; drafts that never went out are not part of the story."""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.facts.builder import VOLATILE, VOLATILE_PREFIXES

MAX_POSTS = 3
MAX_CHANGES = 12


def add_open_questions(s: Session, story_id: int | None, questions: list[str], *, source: str, event_id: int,
                       at) -> None:
    """Append questions to the story (newest last, at most 10 kept); repeats of an open question are skipped."""
    qs = [q.strip() for q in questions if q and q.strip()]
    if not story_id or not qs:
        return
    cur = s.execute(text("SELECT open_questions FROM news_story WHERE id = :s FOR UPDATE"), {"s": story_id}).scalar() or []
    known = {x["question"] for x in cur}
    cur += [{"question": q, "source": source, "event_id": event_id, "at": at.isoformat()} for q in qs if q not in known]
    s.execute(text("UPDATE news_story SET open_questions = CAST(:q AS jsonb) WHERE id = :s"),
              {"q": json.dumps(cur[-10:], ensure_ascii=False), "s": story_id})


def story_memory(s: Session, story_id: int | None, event_id: int, packet: dict, language: str) -> dict | None:
    if not story_id:
        return None
    posts = [dict(r) for r in s.execute(text("""
        SELECT pr.published_at, acc.handle, acc.language, pr.final_text, v.headline, v.open_question,
               v.fact_packet_id, ne.id AS event_id, ne.event_class
        FROM publication_record pr JOIN article a ON a.id = pr.article_id
        JOIN article_version v ON v.id = pr.article_version_id JOIN editorial_account acc ON acc.id = pr.account_id
        JOIN news_event ne ON ne.id = a.news_event_id
        WHERE ne.story_id = :s AND pr.status = 'published'
        ORDER BY pr.published_at DESC LIMIT :n"""), {"s": story_id, "n": MAX_POSTS}).mappings()]
    questions = s.execute(text("SELECT open_questions FROM news_story WHERE id = :s"), {"s": story_id}).scalar() or []
    questions = questions[-5:] + [{"question": p["open_question"], "source": "post", "event_id": p["event_id"]}
                                  for p in posts if p["open_question"]]
    if not posts and not questions:
        return None
    changes = []
    if posts:
        then = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": posts[0]["fact_packet_id"]}).scalar()
        old = {f["id"]: f for f in then["facts"]}
        for f in packet["facts"]:
            o = old.get(f["id"])
            if f["id"] in VOLATILE or f["id"].startswith(VOLATILE_PREFIXES) or f["kind"] == "series":
                continue
            if o is None or o["value"] != f["value"]:
                changes.append({"id": f["id"], "then": (o["display"].get(language) or [None])[0] if o else None,
                                "now": (f["display"].get(language) or [None])[0]})
    return {"published": [{"published_at": p["published_at"].isoformat(), "account": p["handle"],
                           "language": p["language"], "event_class": p["event_class"],
                           "same_event": p["event_id"] == event_id, "headline": p["headline"], "text": p["final_text"]}
                          for p in posts],
            "open_questions": [q["question"] for q in questions],
            "changed_since_last_post": changes[:MAX_CHANGES],
            "note": "Already published on this story, newest first. Do not repeat it; say what changed since."}
