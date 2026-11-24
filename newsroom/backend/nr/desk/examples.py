"""The example library (docs/17): posts the AI reviewer rated ready to post, or that readers clearly liked, become
candidate examples for their style. Numbers and names are turned into placeholders first, so an example shows a
shape, never a fact to copy. A person adopts a candidate; adopting makes a new version of the style."""

import json
import re
from statistics import median

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.guard.numbers import CN_NUMBER, NUMBER

KEEP = 6  # examples a style carries
MIN_PUBLISHABLE = 4
ENGAGEMENT_FACTOR = 2  # at least twice the account's median of the last 30 days...
MIN_SAMPLE = 5  # ...among at least this many posts with metrics


def templatize(body: str, names: list[str]) -> str:
    """Names become {昵称}, {昵称2}...; every figure becomes {数字} (cashtags stay: a coin is not a fact claim)."""
    present = sorted({n for n in names if n and n in body}, key=body.index)  # numbered in order of appearance
    out = body
    for n in sorted(present, key=len, reverse=True):  # longest first, so a name inside another stays whole
        out = out.replace(n, f"\x00{chr(65 + present.index(n))}\x00")  # a letter: the number pass must not touch it

    def number(m: re.Match) -> str:  # only the figure itself: spacing, currency and unit stay as written
        a, b = m.start("num") - m.start(), m.end("num") - m.start()
        return m.group(0)[:a] + "{数字}" + m.group(0)[b:]
    out = NUMBER.sub(number, CN_NUMBER.sub(number, out))
    out = re.sub(r"\x00([A-Z])\x00", lambda m: "{昵称}" if m.group(1) == "A" else f"{{昵称{ord(m.group(1)) - 64}}}", out)
    return re.sub(r"(\{数字\})(\s*\{数字\})+", r"\1", out)


def _names(packet: dict) -> list[str]:
    names = [packet["subject"].get("name"), packet["subject"].get("trader_ref")]
    names += [f["value"] for f in packet["facts"] if f["id"].endswith(".trader") or f["id"] == "trader"
              if isinstance(f["value"], str)]
    return [n for n in names if n and not n.startswith(("0x", "OKX·", "币安"))]


def add_candidate(s: Session, version_id: int, reason: str, score: dict, publication_id: int | None = None) -> int | None:
    v = s.execute(text("""SELECT v.id, v.style_key, v.body, p.content FROM article_version v
                          JOIN fact_packet p ON p.id = v.fact_packet_id WHERE v.id = :v"""), {"v": version_id}).mappings().first()
    if v is None or not v["style_key"]:
        return None
    return s.execute(text("""
        INSERT INTO style_example_candidate (style_key, article_version_id, publication_id, original, example, reason, score)
        VALUES (:k, :v, :p, :o, :e, :r, CAST(:s AS jsonb)) ON CONFLICT (article_version_id) DO NOTHING RETURNING id"""),
        {"k": v["style_key"], "v": version_id, "p": publication_id, "o": v["body"],
         "e": templatize(v["body"], _names(v["content"])), "r": reason, "s": json.dumps(score)}).scalar()


def from_review(s: Session, version_id: int, verdict: str, scores: dict) -> int | None:
    if verdict == "approve" and (scores.get("publishable") or 0) >= MIN_PUBLISHABLE:
        return add_candidate(s, version_id, "review", scores)
    return None


def engagement(m: dict) -> float:
    return (m.get("likes") or 0) + 2 * (m.get("reposts") or 0) + 2 * (m.get("replies") or 0) + (m.get("quotes") or 0)


def from_metrics(s: Session, publication_id: int) -> int | None:
    """A post whose engagement is well above what its account usually gets."""
    p = s.execute(text("SELECT article_version_id, account_id FROM publication_record WHERE id = :p"),
                  {"p": publication_id}).mappings().first()
    if p is None:
        return None
    latest = [dict(r) for r in s.execute(text("""
        SELECT DISTINCT ON (pr.id) pr.id, ps.likes, ps.reposts, ps.replies, ps.quotes
        FROM publication_record pr JOIN performance_snapshot ps ON ps.publication_id = pr.id
        WHERE pr.account_id = :a AND pr.published_at > now() - interval '30 days'
        ORDER BY pr.id, ps.captured_at DESC"""), {"a": p["account_id"]}).mappings()]
    mine = next((r for r in latest if r["id"] == publication_id), None)
    others = [engagement(r) for r in latest if r["id"] != publication_id]
    if mine is None or len(others) < MIN_SAMPLE - 1:
        return None
    base = median(others)
    score = engagement(mine)
    if score >= max(ENGAGEMENT_FACTOR * base, 1):
        return add_candidate(s, p["article_version_id"], "engagement",
                             {"engagement": score, "account_median": base}, publication_id)
    return None


def adopt(s: Session, candidate_id: int, user_id: int) -> int:
    """A new version of the style with this example added (the newest KEEP examples)."""
    c = s.execute(text("SELECT * FROM style_example_candidate WHERE id = :i"), {"i": candidate_id}).mappings().one()
    st = s.execute(text("""SELECT * FROM editorial_style WHERE key = :k ORDER BY version DESC LIMIT 1"""),
                   {"k": c["style_key"]}).mappings().one()
    examples = (list(st["examples"] or []) + [c["example"]])[-KEEP:]
    version = st["version"] + 1
    s.execute(text("""INSERT INTO editorial_style (key, version, language, name, guide, structure, forbidden, examples)
                      VALUES (:k, :v, :l, :n, :g, CAST(:s AS jsonb), CAST(:f AS jsonb), CAST(:e AS jsonb))"""),
              {"k": st["key"], "v": version, "l": st["language"], "n": st["name"], "g": st["guide"],
               "s": json.dumps(st["structure"], ensure_ascii=False), "f": json.dumps(st["forbidden"], ensure_ascii=False),
               "e": json.dumps(examples, ensure_ascii=False)})
    s.execute(text("""UPDATE style_example_candidate SET status = 'adopted', decided_by = :u, decided_at = now()
                      WHERE id = :i"""), {"u": user_id, "i": candidate_id})
    return version
