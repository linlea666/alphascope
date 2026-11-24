"""The AI desk reviewer: after a draft passes the claims guard, it reads it as a native reader and decides whether it
is ready (approve), worth one revision by the desk editor (revise), or a person's call (reject)."""

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.ai.runner import call_json
from nr.ai.schemas import ReviewOut
from nr.jobs.queue import enqueue
from nr.publish import service as pub

MAX_ROUNDS = 2  # one automatic revision, then a person decides


def review_version(sf: sessionmaker, version_id: int, now: datetime, job_id: int | None = None,
                   transport: Callable | None = None) -> dict:
    with sf() as s:
        v = s.execute(text("""
            SELECT v.id, v.article_id, v.headline, v.body, v.thread, v.reply_text, v.facts_used, v.style_key,
                   v.fact_packet_id, a.current_version_id, a.status, a.language, a.news_event_id, acc.handle,
                   acc.display_name, acc.platform, asg.id AS assignment_id
            FROM article_version v JOIN article a ON a.id = v.article_id
            JOIN editorial_account acc ON acc.id = a.account_id JOIN assignment asg ON asg.id = a.assignment_id
            WHERE v.id = :v"""), {"v": version_id}).mappings().first()
        if v is None or v["current_version_id"] != v["id"]:
            return {"skipped": "not the current version"}
        style = s.execute(text("""SELECT name, guide, structure FROM editorial_style WHERE key = :k
                                  ORDER BY version DESC LIMIT 1"""), {"k": v["style_key"]}).mappings().first()
        packet = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": v["fact_packet_id"]}).scalar()
        used = set(v["facts_used"] or [])
        facts = [{"id": f["id"], "says": (f["display"].get(v["language"]) or [None])[0]}
                 for f in packet["facts"] if f["id"] in used]
        rounds = s.execute(text("SELECT count(*) FROM article_review r JOIN article_version x ON x.id = r.article_version_id "
                                "WHERE x.article_id = :a"), {"a": v["article_id"]}).scalar()
    out = call_json(sf, agent_key="reviewer", purpose="draft_review", schema=ReviewOut, semantic=None, now=now,
                    job_id=job_id, event_id=v["news_event_id"], article_id=v["article_id"], transport=transport,
                    inherit_models_from="reporter", language=v["language"],
                    task_vars={"account_handle": v["handle"], "account_name": v["display_name"],
                               "event_id": v["news_event_id"], "style_name": style["name"] if style else "default"},
                    data={"draft": {"headline": v["headline"], "body": v["body"], "thread": v["thread"],
                                    "reply": v["reply_text"]},
                          "style": dict(style) if style else None, "platform": v["platform"],
                          "facts_the_draft_uses": facts})
    r: ReviewOut = out.parsed
    with sf() as s:
        rid = s.execute(text("""
            INSERT INTO article_review (article_version_id, verdict, scores, issues, summary, ai_call_id, model)
            VALUES (:v, :d, CAST(:sc AS jsonb), CAST(:i AS jsonb), :sm, :c, :m) RETURNING id"""),
            {"v": version_id, "d": r.verdict, "sc": json.dumps(r.scores.model_dump()),
             "i": json.dumps([x.model_dump() for x in r.issues], ensure_ascii=False), "sm": r.summary,
             "c": out.call_id, "m": out.model}).scalar_one()
        result: dict = {"review_id": rid, "verdict": r.verdict}
        if r.verdict == "approve" and settings_store.get(s, "review")["ai_ready"]:
            result["held"] = pub.ai_approve(s, v["article_id"], rid)
        elif r.verdict == "revise" and rounds + 1 < MAX_ROUNDS:  # the desk editor gets one go with the notes
            result["revision_job"] = enqueue(s, "draft_generate", {
                "assignment_id": v["assignment_id"], "mode": "polish",
                "review_note": (r.revision_note or r.summary)[:1500],
                "review_issues": [x.model_dump() for x in r.issues]}, dedupe_key=f"revise:{version_id}", priority=45,
                now=now)
        s.commit()
    return result
