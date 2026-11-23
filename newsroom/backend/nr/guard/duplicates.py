"""Near-duplicate detection across accounts: character 3-grams for Chinese, word 3-grams for English. Swapping a few
words keeps most shingles, so light rewording is still caught; a translation is a different language and is judged
by the editor, not here."""

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

PUNCT = re.compile(r"[\s\W_]+", re.U)


def shingles(t: str, lang: str) -> set:
    if lang == "zh":
        s = PUNCT.sub("", t.lower())
        return {s[i:i + 3] for i in range(max(len(s) - 2, 0))}
    words = [w for w in PUNCT.split(t.lower()) if w]
    return {tuple(words[i:i + 3]) for i in range(max(len(words) - 2, 0))}


def similarity(a: str, b: str, lang: str) -> float:
    x, y = shingles(a, lang), shingles(b, lang)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def nearest(s: Session, article_id: int | None, event_id: int, language: str, body: str, hours: int) -> dict | None:
    """Most similar same-language text among other articles about this event or the same trader and coin, and
    published posts, in the last `hours`."""
    rows = s.execute(text("""
        WITH me AS (SELECT trader_id, coin FROM news_event WHERE id = :e)
        SELECT a.id AS article_id, a.account_id, v.headline || ' ' || v.body AS body, 'draft' AS kind
        FROM article a JOIN article_version v ON v.id = a.current_version_id JOIN news_event ne ON ne.id = a.news_event_id
        WHERE a.language = :l AND a.id IS DISTINCT FROM :a AND a.status NOT IN ('killed')
          AND v.created_at > now() - make_interval(hours => :h)
          AND (ne.id = :e OR (ne.trader_id, ne.coin) = (SELECT trader_id, coin FROM me))
        UNION ALL
        SELECT pr.article_id, pr.account_id, pr.final_text, 'published' FROM publication_record pr
        JOIN article a ON a.id = pr.article_id
        WHERE a.language = :l AND pr.article_id IS DISTINCT FROM :a AND pr.status = 'published'
          AND pr.published_at > now() - make_interval(hours => :h)"""),
        {"e": event_id, "l": language, "a": article_id, "h": hours}).mappings().all()
    best = None
    for r in rows:
        sim = similarity(body, r["body"], language)
        if best is None or sim > best["similarity"]:
            best = {"article_id": r["article_id"], "account_id": r["account_id"], "kind": r["kind"],
                    "similarity": round(sim, 3)}
    return best
