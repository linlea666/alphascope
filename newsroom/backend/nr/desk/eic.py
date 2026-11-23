"""The AI editor-in-chief: an explainable coverage recommendation per event, turned into proposed assignments.
It proposes; editors accept or reject. Accounts it keeps silent get no assignment."""

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import audit
from nr.ai.prompts import BEATS
from nr.ai.runner import call_json
from nr.ai.schemas import EicEvaluation
from nr.desk import assignment
from nr.events.states import InvalidTransition, transition
from nr.guard.duplicates import similarity
from nr.stories.memory import add_open_questions


def accounts_brief(s: Session, event_id: int) -> list[dict]:
    rows = s.execute(text("""
        SELECT a.id, a.handle, a.display_name, a.platform, a.venues, a.language, a.positioning, a.audience_profile,
               a.allowed_event_classes, a.long_post_allowed,
               a.min_score, a.daily_post_budget,
               (SELECT json_agg(x) FROM (SELECT v.headline, pr.published_at FROM publication_record pr
                  JOIN article_version v ON v.id = pr.article_version_id
                  WHERE pr.account_id = a.id AND pr.status = 'published' ORDER BY pr.published_at DESC LIMIT 5) x)
                 AS recent_posts
        FROM editorial_account a WHERE a.status = 'active' ORDER BY a.id""")).mappings().all()
    return [dict(r) for r in rows]


def event_brief(s: Session, event_id: int) -> tuple[dict, dict]:
    ev = dict(s.execute(text("""SELECT ne.id, ne.event_class, ne.coin, ne.venue, ne.direction, ne.score, ne.score_breakdown,
                                       ne.state, ne.gate_status, ne.story_id, ne.current_fact_packet_id
                                FROM news_event ne WHERE ne.id = :e"""), {"e": event_id}).mappings().one())
    packet = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": ev["current_fact_packet_id"]}).scalar_one()
    return ev, packet


def story_so_far(s: Session, story_id: int | None, event_id: int) -> list[dict]:
    if not story_id:
        return []
    return [dict(r) for r in s.execute(text("""
        SELECT se.seq, se.role, ne.event_class, ne.coin, ne.direction, ne.detected_at,
               EXISTS (SELECT 1 FROM publication_record pr JOIN article a ON a.id = pr.article_id
                       WHERE a.news_event_id = ne.id AND pr.status = 'published') AS published
        FROM story_event se JOIN news_event ne ON ne.id = se.news_event_id
        WHERE se.story_id = :s AND ne.id <> :e ORDER BY se.seq DESC LIMIT 10"""), {"s": story_id, "e": event_id}).mappings()]


def packet_for_model(packet: dict) -> dict:
    return {"subject": packet["subject"], "event": packet["event"], "constraints": packet["constraints"],
            "facts": [{k: f.get(k) for k in ("id", "kind", "value", "display", "label", "coverage", "derivation")}
                      for f in packet["facts"] if f["kind"] != "series"]}  # curves are for cards, not for writing


def evaluate_event(sf: sessionmaker, event_id: int, now: datetime, job_id: int | None = None,
                   transport: Callable | None = None) -> dict:
    with sf() as s:
        ev, packet = event_brief(s, event_id)
        if ev["state"] not in ("candidate", "evaluated") or ev["gate_status"] != "pass":
            return {"skipped": f"event is {ev['state']} / gate {ev['gate_status']}"}
        accounts = [a for a in accounts_brief(s, event_id) if assignment.account_fits(s, a["id"], event_id)[0]]
        if not accounts:  # e.g. OKX material while no OKX Square account is active: nothing to ask a model
            return {"skipped": "no active account covers this event"}
        story = story_so_far(s, ev["story_id"], event_id)
        if ev["state"] == "candidate":
            transition(s, event_id, "evaluating", actor_kind="ai", reason="editor-in-chief evaluation")
        s.commit()
    ids = [a["id"] for a in accounts]
    fact_ids = {f["id"] for f in packet["facts"]}

    def semantic(o: EicEvaluation) -> list[str]:
        errs = [f"unknown fact id {f}" for f in o.key_fact_ids if f not in fact_ids]
        errs += [f"unknown fact id {f}" for x in o.newsworthiness for f in x.fact_ids if f not in fact_ids]
        seen = [r.account_id for r in o.recommended_accounts] + [r.account_id for r in o.silent_accounts]
        if sorted(seen) != sorted(ids):
            errs.append(f"every account must appear exactly once across recommended and silent: expected {ids}, got {seen}")
        return errs

    try:
        out = call_json(sf, agent_key="eic", purpose="eic_evaluate", schema=EicEvaluation, semantic=semantic, now=now,
                        job_id=job_id, event_id=event_id, transport=transport,
                        task_vars={"event_id": event_id, "event_class": ev["event_class"], "coin": ev["coin"],
                                   "account_ids": ", ".join(map(str, ids))},
                        data={"event": {k: ev[k] for k in ("id", "event_class", "coin", "venue", "direction", "score")},
                              "score_breakdown": ev["score_breakdown"], "fact_packet": packet_for_model(packet),
                              "story_so_far": story, "accounts": accounts,
                              "reporter_roles": {k: v for k, v in BEATS.items()},
                              "reporter_field": "optional per recommended account: reporter_<role> from reporter_roles"})
    except Exception:
        with sf() as s:
            try:
                transition(s, event_id, "candidate", actor_kind="system", reason="evaluation failed; back to the pool")
                s.commit()
            except InvalidTransition:
                pass
        raise
    o: EicEvaluation = out.parsed
    lang = {a["id"]: a["language"] for a in accounts}
    flags = set(o.risk_flags)
    recs = o.recommended_accounts
    similar: set[int] = set()  # same-language accounts with near-identical angles: a person must decide
    for i, r in enumerate(recs):
        for q in recs[i + 1:]:
            if lang[r.account_id] == lang[q.account_id] and similarity(r.angle, q.angle, lang[r.account_id]) >= 0.6:
                similar |= {r.account_id, q.account_id}
    if similar:
        flags.add("duplicate_risk")
    with sf() as s:
        payload = o.model_dump()
        payload["risk_flags"] = sorted(flags)
        eid = s.execute(text("""INSERT INTO eic_evaluation (news_event_id, fact_packet_id, ai_call_id, output, decision,
                                                            priority, created_at)
                                VALUES (:e, :p, :c, CAST(:o AS jsonb), :d, :pr, :n) RETURNING id"""),
                        {"e": event_id, "p": ev["current_fact_packet_id"], "c": out.call_id,
                         "o": json.dumps(payload, ensure_ascii=False), "d": o.decision, "pr": o.priority, "n": now}
                        ).scalar_one()
        created = []
        if o.decision == "cover":
            for r in recs:
                aid = assignment.create(s, event_id, r.account_id, source="eic_ai", angle=r.angle, reason=r.reason,
                                        fmt=r.format, eic_evaluation_id=eid, needs_human=r.account_id in similar,
                                        agent_key=r.reporter)
                if aid:
                    created.append(aid)
        s.execute(text("UPDATE news_event SET priority = :p WHERE id = :e"), {"p": o.priority, "e": event_id})
        add_open_questions(s, ev["story_id"], o.open_questions, source="eic", event_id=event_id, at=now)
        try:
            transition(s, event_id, "evaluated", actor_kind="ai", reason=f"{o.decision} {o.priority}")
        except InvalidTransition:
            pass
        audit.record(s, "eic.evaluate", "news_event", event_id, actor_kind="ai",
                     after={"evaluation_id": eid, "decision": o.decision, "assignments": created})
        s.commit()
    return {"evaluation_id": eid, "decision": o.decision, "assignments": created, "flags": sorted(flags)}
