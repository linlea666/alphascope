"""The prompt coach (docs/17): once a week, for each role whose drafts the AI reviewer read at least MIN_REVIEWS times,
the writing part of its current prompt is redrafted against the issues the reviewer kept raising. The facts rules
above the writing part are kept byte for byte (the coach never sees them as editable); the draft becomes a new prompt
version that nothing uses yet, and an experiment compares it with the current one. A person adopts or rejects it."""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import audit, settings_store
from nr.ai.runner import call_json, resolve_prompt
from nr.ai.schemas import CoachOut
from nr.feed.client import FeedClient
from nr.jobs.queue import enqueue

log = logging.getLogger(__name__)
MIN_REVIEWS = 5
MAX_ISSUES = 40
EXPERIMENT_EVENTS = 5
# where the writing part starts: everything before it (the facts rules) is kept as it is
MARKERS = ("\n写法：", "\n写法:", "\nHow to write it:")
# names the writing part may carry that the drafting code reads: a draft must keep every one it had
KEEP = ("hooks[0]", "data.hooks", "lede_fact_ids", "facts_used", "data.persona", "data.closing", "data.closing_question",
        "data.series", "data.naming", "story_memory", "why_now", "open_question", "subject.trader_ref", "change.recency")
# roles an experiment can sample events for (it samples position events; the square storyteller writes columns)
EXPERIMENTABLE = ("reporter", "reporter_breaking", "reporter_investigator", "reporter_data", "reporter_derivatives",
                  "reporter_historian", "reporter_star")


def split(system: str) -> tuple[str, str, str] | None:
    """(facts part, the writing heading, the writing body) at the first writing marker; None when there is none."""
    for m in MARKERS:
        if m in system:
            i = system.index(m) + 1
            head, _, body = system[i:].partition("\n")
            return system[:i], head, body
    return None


def groups(s: Session, since: datetime) -> list[dict]:
    """Each active role's current prompt with the reviews its drafts got since `since`."""
    out = []
    for ag in s.execute(text("""SELECT agent_key, prompt_key, prompt_version FROM ai_agent_profile
                                WHERE active AND agent_key NOT IN ('reviewer', 'prompt_coach', 'eic')
                                ORDER BY agent_key""")).mappings():
        for lang in ("zh", "en"):
            ref = f"{ag['prompt_key']}@v{ag['prompt_version']}"
            rows = s.execute(text("""
                SELECT r.scores, r.issues FROM article_review r JOIN article_version v ON v.id = r.article_version_id
                WHERE r.created_at > :since AND (v.prompt_ref = :ref OR v.prompt_ref = :ref || '.' || :l)
                  AND v.agent_key = :k"""),
                {"since": since, "ref": ref, "l": lang, "k": ag["agent_key"]}).mappings().all()
            if len(rows) < MIN_REVIEWS:
                continue
            issues = [i for r in rows for i in (r["issues"] or [])][:MAX_ISSUES]
            dims = {d for r in rows for d in (r["scores"] or {})}
            scores = {d: round(sum((r["scores"] or {}).get(d, 0) for r in rows) / len(rows), 2) for d in sorted(dims)}
            out.append({**dict(ag), "language": lang, "n": len(rows), "issues": issues, "scores": scores})
    return out


def redraft(sf: sessionmaker, g: dict, base: dict, now: datetime, transport: Callable | None) -> tuple[str, CoachOut, int]:
    """The new full system text for one language of one role: its facts part kept, the writing body redrafted."""
    facts_part, heading, body = split(base["system_text"])
    kept = [k for k in KEEP if k in body]

    def semantic(o: CoachOut) -> list[str]:
        errs = [f"the writing part must keep '{k}' exactly as it is" for k in kept if k not in o.writing]
        if not 0.6 * len(body) <= len(o.writing) <= 1.8 * len(body):
            errs.append(f"keep the writing part about as long as it is now ({len(body)} characters)")
        if any(m.strip() in o.writing for m in MARKERS):
            errs.append("write only the body of the writing part, without its heading")
        return errs

    out = call_json(sf, agent_key="prompt_coach", purpose="prompt_coach", schema=CoachOut, semantic=semantic, now=now,
                    transport=transport, language="zh", inherit_models_from="reporter",
                    task_vars={"prompt_key": g["prompt_key"], "prompt_version": g["prompt_version"],
                               "prompt_language": g["language"], "n_reviews": g["n"]},
                    data={"current_writing": body, "language": g["language"], "review_issues": g["issues"],
                          "scores": g["scores"]})
    return facts_part + heading + "\n" + out.parsed.writing.strip("\n"), out.parsed, out.call_id


def run(sf: sessionmaker, client: FeedClient | None, now: datetime, transport: Callable | None = None,
        only: str | None = None) -> dict:
    """One new version per role (key) with drafts: languages without a draft keep their current text in it, so
    switching to the new version changes only what the coach redrafted."""
    with sf() as s:
        todo = [g for g in groups(s, now - timedelta(days=7)) if only in (None, g["agent_key"])]
    by_key: dict[str, list[dict]] = {}
    for g in todo:
        by_key.setdefault(g["prompt_key"], []).append(g)
    made = []
    for key, gs in by_key.items():
        drafts = []
        for g in gs:
            with sf() as s:
                base = resolve_prompt(s, key, g["prompt_version"], g["language"])
                waiting = s.execute(text("""SELECT 1 FROM prompt_proposal WHERE prompt_key = :k AND base_version = :v
                                            AND status IN ('draft', 'testing')"""),
                                    {"k": key, "v": g["prompt_version"]}).scalar()
            if base is None or split(base["system_text"]) is None or waiting:
                continue  # nothing to redraft, or last week's draft for this version still waits for a decision
            system, o, call_id = redraft(sf, g, base, now, transport)
            drafts.append((g, base, system, o, call_id))
        if not drafts:
            continue
        with sf() as s:
            version = s.execute(text("SELECT coalesce(max(version), 0) + 1 FROM prompt_template WHERE key = :k"),
                                {"k": key}).scalar()
            base_version = drafts[0][0]["prompt_version"]
            redrafted = {g["language"] for g, *_ in drafts}
            rows = [(g["language"], system, base["user_template"]) for g, base, system, _, _ in drafts]
            rows += [(r["language"], r["system_text"], r["user_template"]) for r in s.execute(text(
                "SELECT * FROM prompt_template WHERE key = :k AND version = :v"), {"k": key, "v": base_version}).mappings()
                if r["language"] not in redrafted]
            for lang, system, user in rows:
                s.execute(text("""INSERT INTO prompt_template (key, version, language, system_text, user_template, sha256, notes)
                                  VALUES (:k, :v, :l, :s, :u, :h, 'prompt coach draft')"""),
                          {"k": key, "v": version, "l": lang, "s": system, "u": user,
                           "h": hashlib.sha256((system + user).encode()).hexdigest()})
            for g, base, system, o, call_id in drafts:
                pid = s.execute(text("""
                    INSERT INTO prompt_proposal (prompt_key, language, base_version, version, rationale, changes, issues,
                                                 ai_call_id)
                    VALUES (:k, :l, :b, :v, :r, CAST(:c AS jsonb), CAST(:i AS jsonb), :call) RETURNING id"""),
                    {"k": key, "l": g["language"], "b": g["prompt_version"], "v": version, "r": o.rationale,
                     "c": json.dumps([c.model_dump() for c in o.changes], ensure_ascii=False),
                     "i": json.dumps({"n_reviews": g["n"], "scores": g["scores"]}, ensure_ascii=False),
                     "call": call_id}).scalar_one()
                name = experiment_for(s, pid, g, version, now)
                audit.record(s, "prompt.coach_draft", "prompt", f"{key}@v{version}", actor_kind="ai",
                             after={"proposal": pid, "base": g["prompt_version"], "language": g["language"],
                                    "experiment": name})
                made.append({"proposal": pid, "prompt": f"{key}@v{version}.{g['language']}", "experiment": name})
            s.commit()
    return {"groups": len(todo), "proposals": made}


def experiment_for(s: Session, proposal_id: int, g: dict, version: int, now: datetime) -> str | None:
    """A small experiment, current against draft, on the account the role wrote for most this week, with the role's
    model; None when the role writes no position events (nothing to sample) or has no model."""
    if g["agent_key"] not in EXPERIMENTABLE:
        return None
    acc = s.execute(text("""
        SELECT a.account_id FROM article_version v JOIN article a ON a.id = v.article_id
        JOIN editorial_account acc ON acc.id = a.account_id
        WHERE v.agent_key = :k AND acc.language = :l AND v.created_at > :since
        GROUP BY a.account_id ORDER BY count(*) DESC LIMIT 1"""),
        {"k": g["agent_key"], "l": g["language"], "since": now - timedelta(days=7)}).scalar()
    model = s.execute(text("""SELECT coalesce(a.model_config_id, r.model_config_id) FROM ai_agent_profile a
                              LEFT JOIN ai_agent_profile r ON r.agent_key = 'reporter' AND r.active
                              WHERE a.agent_key = :k AND a.active"""), {"k": g["agent_key"]}).scalar()
    if acc is None or model is None or not settings_store.get(s, "ai")["enabled"]:
        return None
    name = re.sub(r"[^a-z0-9-]+", "-", f"coach-{g['agent_key']}-v{version}-{g['language']}".lower())[:60]
    uid = s.execute(text("SELECT id FROM app_user ORDER BY id LIMIT 1")).scalar()
    enqueue(s, "experiment_create", {"name": name, "account_id": acc, "model_config_id": model, "n": EXPERIMENT_EVENTS,
                                     "user_id": uid, "agent_key": g["agent_key"],
                                     "arms": {"a": {"prompt_version": g["prompt_version"]}, "b": {"prompt_version": version}}},
            dedupe_key=f"experiment:{name}", priority=50, now=now)
    s.execute(text("UPDATE prompt_proposal SET experiment_name = :x, status = 'testing' WHERE id = :p"),
              {"x": name, "p": proposal_id})
    return name
