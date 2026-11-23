"""AI reporter drafts. Each draft is written for one accepted assignment, from the event's current fact packet, in the
account's language; zh and en drafts are written independently from the same packet version, never translated."""

from collections.abc import Callable
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr.ai.runner import call_json
from nr.ai.schemas import DraftOut
from nr.articles.versions import Draft, save_version
from nr.desk.eic import packet_for_model


def generate_draft(sf: sessionmaker, assignment_id: int, now: datetime, job_id: int | None = None,
                   transport: Callable | None = None, user_id: int | None = None) -> dict:
    with sf() as s:
        a = s.execute(text("""
            SELECT asg.*, acc.handle, acc.display_name, acc.language, acc.positioning, acc.audience_profile,
                   acc.long_post_allowed, acc.style_key, ar.id AS article_id, ne.current_fact_packet_id, ne.event_class,
                   ne.coin, ne.gate_status
            FROM assignment asg JOIN editorial_account acc ON acc.id = asg.account_id
            JOIN news_event ne ON ne.id = asg.news_event_id LEFT JOIN article ar ON ar.assignment_id = asg.id
            WHERE asg.id = :i"""), {"i": assignment_id}).mappings().first()
        if a is None or a["status"] not in ("accepted", "drafting") or a["article_id"] is None:
            raise HTTPException(409, "only accepted assignments are drafted")
        if a["gate_status"] != "pass":
            return {"skipped": "event gate is not passing"}
        packet = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": a["current_fact_packet_id"]}
                           ).scalar_one()
        style = s.execute(text("""SELECT name, guide, structure, forbidden, examples FROM editorial_style WHERE key = :k
                                  ORDER BY version DESC LIMIT 1"""), {"k": a["style_key"]}).mappings().first()
        others = [dict(r) for r in s.execute(text("""
            SELECT acc.handle, acc.language, v.angle, v.headline FROM article ar
            JOIN article_version v ON v.id = ar.current_version_id JOIN editorial_account acc ON acc.id = ar.account_id
            WHERE ar.news_event_id = :e AND ar.id <> :a AND ar.status <> 'killed'"""),
            {"e": a["news_event_id"], "a": a["article_id"]}).mappings()]
    fact_ids = {f["id"] for f in packet["facts"]}

    def semantic(o: DraftOut) -> list[str]:
        errs = [f"unknown fact id {f}" for f in o.facts_used if f not in fact_ids]
        if o.language != a["language"]:
            errs.append(f"language must be {a['language']}")
        return errs

    out = call_json(
        sf, agent_key="reporter", purpose="draft_generate", schema=DraftOut, semantic=semantic, now=now, job_id=job_id,
        event_id=a["news_event_id"], article_id=a["article_id"], transport=transport,
        task_vars={"language": a["language"], "account_handle": a["handle"], "account_name": a["display_name"],
                   "event_id": a["news_event_id"], "angle": a["angle"] or "(editor's choice)", "format": a["format"],
                   "style_name": style["name"] if style else "default"},
        data={"fact_packet": packet_for_model(packet),
              "account": {"handle": a["handle"], "language": a["language"], "positioning": a["positioning"],
                          "audience": a["audience_profile"], "long_posts_allowed": a["long_post_allowed"]},
              "style": dict(style) if style else None, "assignment": {"angle": a["angle"], "reason": a["reason"],
                                                                      "format": a["format"]},
              "other_drafts_for_this_event": others})
    o: DraftOut = out.parsed
    with sf() as s:
        v = save_version(s, a["article_id"], Draft(
            body=o.body, headline=o.headline_candidates[0], alt_headlines=o.headline_candidates[1:], thread=o.thread,
            angle=o.angle, added_value=o.added_value, difference_from_others=o.difference_from_others,
            why_shareable=o.why_shareable, risks=o.risks, facts_used=o.facts_used),
            origin="ai", ai_call_id=out.call_id, agent_key=out.agent_key, prompt_ref=out.prompt_ref, model=out.model)
        s.execute(text("UPDATE assignment SET status = 'drafting' WHERE id = :i AND status = 'accepted'"),
                  {"i": assignment_id})
        s.commit()
    return {"article_id": a["article_id"], "version_id": v["id"], "guard": v["guard_status"],
            "made_current": v["made_current"]}
