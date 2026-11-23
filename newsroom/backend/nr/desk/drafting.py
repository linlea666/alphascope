"""AI drafts for one accepted assignment, always from the event's current fact packet and in the account's language;
zh and en drafts are written independently from the same packet version, never translated.

Modes: draft (the assigned reporter role), polish (the zh/en desk editor reworks the current version) and fix (the fact
checker rewrites the current version to clear the guard's findings). A model can be chosen per run; comparison runs
add versions without replacing the current one.

The output schema follows the prompt: a prompt that asks for lede_fact_ids (reporter v2) gets the v2 schema and the
story memory; one that reads hooks (v3) also gets the ranked angles, a character budget, the reporter's house style
and a fresh recency. The "baseline" variant writes the way facts-1 packets did, without contrast facts, for A/B
experiments. The full address and an explorer link are added by code as the thread's last post, never by a model.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.ai.runner import call_json, resolve_prompt
from nr.ai.schemas import DraftOut, DraftOutV2
from nr.articles.versions import Draft, current_version, save_version
from nr.cards.render import render_for_packet
from nr.cards.v2 import pick_card
from nr.desk.eic import packet_for_model
from nr.desk.hooks import hooks
from nr.facts.builder import T, recency
from nr.guard.xlength import weighted_length
from nr.stories.memory import story_memory

log = logging.getLogger(__name__)
MODES = ("draft", "polish", "fix")
CONTRAST_PREFIXES = ("contrast.", "radar.", "book.", "crowd.", "trader.alltime_pnl", "trader.month_pnl")
CONTRAST_CONSTRAINTS = {"CROWD_SAMPLE", "HEDGE_POSSIBLE", "ARCHIVE_PARTIAL", "RADAR_SINCE", "LEADERBOARD_PNL"}


def char_budget(rule: dict | None, language: str, long_ok: bool) -> int:
    """The body's length in plain characters, which a model can count (X weighs CJK double; keep a margin)."""
    if not rule or long_ok or not rule["weighted"]:
        cap = 300 if language == "zh" else 600
        return min(cap, int(rule["limit"] * 0.9)) if rule and not rule["weighted"] else cap
    return int(rule["limit"] * 0.86) // 2 if language == "zh" else rule["limit"] - 30


def fresh_recency(packet: dict, now) -> dict:
    """change.recency in the words that are true now, or gone once it is over a day old."""
    out = []
    for f in packet["facts"]:
        if f["id"] == "change.recency":
            r = recency(T(f["as_of"]), now)
            if r is None:
                continue
            f = {**f, "value": r[0], "display": r[1]}
        out.append(f)
    return {**packet, "facts": out}


def link_post(packet: dict, language: str) -> str | None:
    subj = packet.get("subject") or {}
    if not subj.get("explorer_url"):
        return None
    label = "地址：" if language == "zh" else "Address: "
    return f"{label}{subj['trader_key']}\n{subj['explorer_url']}"


def baseline_packet(packet: dict) -> dict:
    """The packet as facts-1 built it: no comparison with the trader's past, their book or the crowd."""
    return {**packet, "facts": [f for f in packet["facts"] if not f["id"].startswith(CONTRAST_PREFIXES)],
            "constraints": [c for c in packet["constraints"] if c["code"] not in CONTRAST_CONSTRAINTS]}


def generate_draft(sf: sessionmaker, assignment_id: int, now: datetime, job_id: int | None = None,
                   transport: Callable | None = None, user_id: int | None = None, mode: str = "draft",
                   model_config_id: int | None = None, compare: bool = False, prompt_version: int | None = None,
                   variant: str | None = None, style_key: str | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    with sf() as s:
        a = s.execute(text("""
            SELECT asg.*, acc.handle, acc.display_name, acc.language, acc.positioning, acc.audience_profile, acc.platform,
                   acc.long_post_allowed, acc.link_in_thread, asg.style_key AS asg_style, acc.style_key AS acc_style,
                   ar.id AS article_id,
                   ne.current_fact_packet_id, ne.event_class, ne.coin, ne.gate_status, ne.story_id
            FROM assignment asg JOIN editorial_account acc ON acc.id = asg.account_id
            JOIN news_event ne ON ne.id = asg.news_event_id LEFT JOIN article ar ON ar.assignment_id = asg.id
            WHERE asg.id = :i"""), {"i": assignment_id}).mappings().first()
        if a is None or a["status"] not in ("accepted", "drafting") or a["article_id"] is None:
            raise HTTPException(409, "only accepted assignments are drafted")
        if a["gate_status"] != "pass" and a["source"] != "trial":  # a trial may run on any event; it can't be approved
            return {"skipped": "event gate is not passing"}
        packet = s.execute(text("SELECT content FROM fact_packet WHERE id = :p"), {"p": a["current_fact_packet_id"]}
                           ).scalar_one()
        others = [dict(r) for r in s.execute(text("""
            SELECT acc.handle, acc.language, v.angle, v.headline FROM article ar
            JOIN article_version v ON v.id = ar.current_version_id JOIN editorial_account acc ON acc.id = ar.account_id
            WHERE ar.news_event_id = :e AND ar.id <> :a AND ar.status <> 'killed'"""),
            {"e": a["news_event_id"], "a": a["article_id"]}).mappings()]
        current = current_version(s, a["article_id"])
        platform_rule = {k: v for k, v in settings_store.get(s, "platforms").get(a["platform"], {}).items() if k != "hosts"}
        agent = {"draft": a["agent_key"] or "reporter", "polish": f"editor_{a['language']}", "fix": "fact_checker"}[mode]
        g = s.execute(text("SELECT prompt_key, prompt_version FROM ai_agent_profile WHERE agent_key = :k AND active"),
                      {"k": agent}).mappings().first()
        row = resolve_prompt(s, g["prompt_key"], prompt_version or g["prompt_version"], a["language"]) if g else None
        if prompt_version and row is None:
            raise HTTPException(422, f"{agent} has no prompt version {prompt_version}")
        prompt = row["system_text"] if row else None
        v2 = "lede_fact_ids" in (prompt or "")
        v3 = "hooks[0]" in (prompt or "")
        # style: chosen for this run > set on the assignment > the reporter's house style (v3) > the account's
        house = settings_store.get(s, "styles")["by_reporter"].get(a["agent_key"] or "reporter", "") \
            .replace("{language}", a["language"]) if v3 else None
        style = None
        for key in (style_key, a["asg_style"], house, a["acc_style"]):
            if key and (style := s.execute(text("""SELECT key, name, guide, structure, forbidden, examples
                                                    FROM editorial_style WHERE key = :k ORDER BY version DESC LIMIT 1"""),
                                           {"k": key}).mappings().first()):
                break
        if variant == "baseline":
            packet = baseline_packet(packet)
        elif v3:
            packet = fresh_recency(packet, now)
        memory = story_memory(s, a["story_id"], a["news_event_id"], packet, a["language"]) \
            if v2 and variant != "baseline" else None
    if mode != "draft" and current is None:
        raise HTTPException(409, "there is no draft to revise yet")
    schema = DraftOutV2 if v2 else DraftOut
    fact_ids = {f["id"] for f in packet["facts"]}
    budget = char_budget(platform_rule, a["language"], a["long_post_allowed"])
    link = link_post(packet, a["language"]) if a["link_in_thread"] and variant != "baseline" else None
    url = (packet.get("subject") or {}).get("explorer_url")
    in_body = bool(url and style and (style["structure"] or {}).get("link_in_body") and variant != "baseline")
    if in_body:  # the link closes the post itself: X counts it as 23, keep room for it
        budget -= 14 if a["language"] == "zh" else 26
        link = None

    def semantic(o: DraftOut) -> list[str]:
        errs = [f"unknown fact id {f}" for f in o.facts_used if f not in fact_ids]
        errs += [f"unknown fact id {f} in lede_fact_ids" for f in getattr(o, "lede_fact_ids", []) if f not in fact_ids]
        if o.language != a["language"]:
            errs.append(f"language must be {a['language']}")
        # same rule as the guard's TOO_LONG for every post, so the model gets one chance to shorten it itself
        if platform_rule and not a["long_post_allowed"]:
            for name, post in [("body", o.body), *((f"thread post {i}", t) for i, t in enumerate(o.thread or [], 1))]:
                n = weighted_length(post) if platform_rule["weighted"] else len(post)
                if n > platform_rule["limit"]:
                    errs.append(f"{name} has {len(post)} characters, over the platform limit: keep it within {budget} "
                                "characters (cut a number or a line)")
        return errs

    data = {"fact_packet": packet_for_model(packet),
            "account": {"handle": a["handle"], "language": a["language"], "positioning": a["positioning"],
                        "audience": a["audience_profile"], "long_posts_allowed": a["long_post_allowed"],
                        "platform": a["platform"], "platform_rule": platform_rule,
                        "note": "Write for this platform. The length rule here replaces any X limit in your instructions."},
            "style": {k: v for k, v in style.items() if k != "key"} if style else None,
            "assignment": {"angle": a["angle"], "reason": a["reason"], "format": a["format"]},
            "other_drafts_for_this_event": others}
    if memory:
        data["story_memory"] = memory
    if v3 and variant != "baseline":
        data["hooks"] = hooks(packet, a["language"], memory, (style["structure"] or {}).get("prefer_hooks") if style else None)
    if mode != "draft":
        data["current_draft"] = {k: current[k] for k in ("headline", "body", "thread", "facts_used", "angle")}
    if mode == "fix":
        data["guard_findings"] = [{k: f.get(k) for k in ("part", "code", "text", "detail")}
                                  for f in current["guard_report"]["findings"]]
    out = call_json(
        sf, agent_key=agent, purpose="draft_generate" if mode == "draft" else f"draft_{mode}", schema=schema,
        semantic=semantic, now=now, job_id=job_id, prompt_version=prompt_version, language=a["language"],
        event_id=a["news_event_id"], article_id=a["article_id"], transport=transport,
        model_override=model_config_id or a["model_config_id"], inherit_models_from=None if agent == "reporter"
        else "reporter",
        task_vars={"language": a["language"], "account_handle": a["handle"], "account_name": a["display_name"],
                   "event_id": a["news_event_id"], "angle": a["angle"] or "(editor's choice)", "format": a["format"],
                   "style_name": style["name"] if style else "default", "char_budget": budget},
        data=data)
    o: DraftOut = out.parsed
    card = pick_card(packet, [h["key"] for h in data["hooks"]]) if data.get("hooks") is not None else None
    thread = list(o.thread or [])
    if link or in_body:  # the address and link come from the packet, never from a model; exactly one copy
        key = packet["subject"]["trader_key"]
        thread = [t for t in thread if key not in t and "hypurrscan.io" not in t] + ([link] if link else [])
    body = o.body
    if in_body:
        key = packet["subject"]["trader_key"]
        lines = [ln for ln in body.rstrip().splitlines() if key not in ln and "hypurrscan.io" not in ln]
        body = "\n".join(lines).rstrip() + "\n\n" + url
    with sf() as s:
        v = save_version(s, a["article_id"], Draft(
            body=body, headline=o.headline_candidates[0], alt_headlines=o.headline_candidates[1:], thread=thread or None,
            angle=o.angle, added_value=o.added_value, difference_from_others=o.difference_from_others,
            why_shareable=o.why_shareable, risks=o.risks, facts_used=o.facts_used,
            why_now=getattr(o, "why_now", ""), open_question=getattr(o, "open_question", None),
            lede_fact_ids=getattr(o, "lede_fact_ids", None), card_template=card),
            origin="ai", ai_call_id=out.call_id, agent_key=out.agent_key, prompt_ref=out.prompt_ref, model=out.model,
            make_current=False if compare else None)
        s.execute(text("UPDATE assignment SET status = 'drafting' WHERE id = :i AND status = 'accepted'"),
                  {"i": assignment_id})
        s.commit()
    if card:  # the image that goes with the post; a card that fails to draw never costs the draft
        try:
            render_for_packet(sf, a["current_fact_packet_id"], card, a["language"])
        except Exception:  # noqa: BLE001
            log.exception("card %s for packet %s", card, a["current_fact_packet_id"])
    return {"article_id": a["article_id"], "version_id": v["id"], "guard": v["guard_status"],
            "made_current": v["made_current"], "model": out.model, "agent": agent, "mode": mode}
