"""Articles and their immutable versions. Every save is a new version that records who or what wrote it, the exact
fact packet it was written from, the facts it cites and the guard's verdict. An AI version never replaces a human
one as current unless a person accepts it."""

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import settings_store
from nr.guard import duplicates
from nr.guard.run import GUARD_VERSION
from nr.guard.run import run as run_guard

EDITABLE = ("drafting", "draft_ready", "changes_requested", "stale", "in_review")


@dataclass
class Draft:
    body: str
    headline: str = ""
    alt_headlines: list | None = None
    thread: list | None = None
    angle: str = ""
    added_value: str = ""
    difference_from_others: str = ""
    why_shareable: str = ""
    risks: list | None = None
    facts_used: list | None = None


def text_hash(headline: str, body: str, thread: list | None) -> str:
    return hashlib.sha256("\n".join([headline or "", body or "", *(thread or [])]).encode()).hexdigest()


def parts_of(headline: str, body: str, thread: list | None) -> dict[str, str]:
    p = {"headline": headline or "", "body": body or ""}
    for i, t in enumerate(thread or [], start=1):
        p[f"thread.{i}"] = t
    return p


def load(s: Session, article_id: int) -> dict:
    a = s.execute(text("""
        SELECT a.*, ne.current_fact_packet_id, ne.gate_status, ne.state AS event_state, acc.long_post_allowed,
               acc.style_key, acc.handle, acc.status AS account_status, acc.platform
        FROM article a JOIN news_event ne ON ne.id = a.news_event_id JOIN editorial_account acc ON acc.id = a.account_id
        WHERE a.id = :i"""), {"i": article_id}).mappings().first()
    if a is None:
        raise HTTPException(404, "article not found")
    return dict(a)


def style_forbidden(s: Session, style_key: str | None) -> list[str]:
    if not style_key:
        return []
    row = s.execute(text("SELECT forbidden FROM editorial_style WHERE key = :k ORDER BY version DESC LIMIT 1"),
                    {"k": style_key}).scalar()
    return list(row or [])


def guard(s: Session, art: dict, packet_id: int, parts: dict[str, str], facts_used: list[str]) -> dict:
    packet = s.execute(text("SELECT content FROM fact_packet WHERE id = :i"), {"i": packet_id}).scalar_one()
    g = settings_store.get(s, "guard")
    dup = duplicates.nearest(s, art["id"], art["news_event_id"], art["language"],
                             f"{parts.get('headline', '')} {parts.get('body', '')}", g["duplicate_hours"])
    return run_guard(parts, packet, facts_used=facts_used, version_packet_id=packet_id,
                     current_packet_id=art["current_fact_packet_id"], long_post_allowed=art["long_post_allowed"],
                     allow=g["allow_numbers"], forbidden=style_forbidden(s, art["style_key"]), duplicate=dup,
                     dup_block=g["duplicate_block"], dup_warn=g["duplicate_warn"],
                     platform=settings_store.get(s, "platforms").get(art["platform"]))


def save_version(s: Session, article_id: int, d: Draft, *, origin: str, user_id: int | None = None,
                 ai_call_id: int | None = None, agent_key: str | None = None, prompt_ref: str | None = None,
                 model: str | None = None, fact_packet_id: int | None = None, make_current: bool | None = None) -> dict:
    art = load(s, article_id)
    if art["status"] not in EDITABLE:
        raise HTTPException(409, f"article is {art['status']}")
    packet_id = fact_packet_id or art["current_fact_packet_id"]
    packet_version = s.execute(text("SELECT version FROM fact_packet WHERE id = :i AND news_event_id = :e"),
                               {"i": packet_id, "e": art["news_event_id"]}).scalar()
    if packet_version is None:
        raise HTTPException(422, "fact packet does not belong to this event")
    current = s.execute(text("SELECT id, origin FROM article_version WHERE id = :i"),
                        {"i": art["current_version_id"]}).mappings().first() if art["current_version_id"] else None
    if make_current is None:  # people's edits are never overwritten by a model
        make_current = origin == "human" or current is None or current["origin"] == "ai"
    facts_used = list(d.facts_used or [])
    report = guard(s, art, packet_id, parts_of(d.headline, d.body, d.thread), facts_used)
    no = s.execute(text("SELECT coalesce(max(version_no), 0) + 1 FROM article_version WHERE article_id = :a"),
                   {"a": article_id}).scalar()
    v = s.execute(text("""
        INSERT INTO article_version (article_id, version_no, parent_version_id, origin, author_user_id, ai_call_id,
          agent_key, prompt_ref, model, fact_packet_id, fact_packet_version, headline, alt_headlines, body, thread,
          angle, added_value, difference_from_others, why_shareable, risks, facts_used, text_hash, guard_status,
          guard_report, guard_version)
        VALUES (:a, :no, :parent, :o, :u, :ai, :agent, :pr, :model, :p, :pv, :h, CAST(:alt AS jsonb), :b,
          CAST(:th AS jsonb), :angle, :av, :diff, :why, CAST(:risks AS jsonb), :fu, :hash, :gs, CAST(:gr AS jsonb), :gv)
        RETURNING id, version_no, guard_status"""),
        {"a": article_id, "no": no, "parent": current["id"] if current else None, "o": origin, "u": user_id,
         "ai": ai_call_id, "agent": agent_key, "pr": prompt_ref, "model": model, "p": packet_id, "pv": packet_version,
         "h": d.headline or "", "alt": json.dumps(d.alt_headlines or [], ensure_ascii=False), "b": d.body,
         "th": json.dumps(d.thread, ensure_ascii=False) if d.thread else None, "angle": d.angle,
         "av": d.added_value, "diff": d.difference_from_others, "why": d.why_shareable,
         "risks": json.dumps(d.risks or [], ensure_ascii=False), "fu": facts_used,
         "hash": text_hash(d.headline, d.body, d.thread), "gs": report["status"],
         "gr": json.dumps(report, ensure_ascii=False, default=str), "gv": GUARD_VERSION}).mappings().one()
    for fid in dict.fromkeys(facts_used):
        s.execute(text("""INSERT INTO article_fact_reference (article_version_id, fact_packet_id, fact_id)
                          VALUES (:v, :p, :f) ON CONFLICT DO NOTHING"""), {"v": v["id"], "p": packet_id, "f": fid})
    if make_current:
        s.execute(text("""UPDATE article SET current_version_id = :v, status = 'draft_ready', stale_reason = NULL,
                                 updated_at = now() WHERE id = :a"""), {"v": v["id"], "a": article_id})
    return {**dict(v), "made_current": make_current, "guard": report}


def accept_version(s: Session, article_id: int, version_id: int) -> None:
    art = load(s, article_id)
    if art["status"] not in EDITABLE:
        raise HTTPException(409, f"article is {art['status']}")
    ok = s.execute(text("SELECT 1 FROM article_version WHERE id = :v AND article_id = :a"),
                   {"v": version_id, "a": article_id}).scalar()
    if not ok:
        raise HTTPException(404, "version not found")
    s.execute(text("""UPDATE article SET current_version_id = :v, status = 'draft_ready', updated_at = now()
                      WHERE id = :a"""), {"v": version_id, "a": article_id})


def current_version(s: Session, article_id: int) -> dict | None:
    row = s.execute(text("""SELECT v.* FROM article a JOIN article_version v ON v.id = a.current_version_id
                            WHERE a.id = :a"""), {"a": article_id}).mappings().first()
    return dict(row) if row else None


def set_status(s: Session, article_id: int, to: str) -> None:
    s.execute(text("UPDATE article SET status = :t, updated_at = now() WHERE id = :a"), {"t": to, "a": article_id})
