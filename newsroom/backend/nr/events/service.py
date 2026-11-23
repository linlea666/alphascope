"""Turning detection candidates into unique news events.

Idempotency: an event's dedupe_key is derived from (venue, trader, coin, episode, class, escalation level), so the
same position seen again by a later poll, or a replayed feed page, maps to the same event. Increases merge into the
episode's open whale_add / underwater_add event until something about it is published; after that only a higher size
tier starts a new event (a follow-up in the same story).
"""

import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.detect.rules import MERGING, RULE_VERSION, Candidate
from nr.events.states import OPEN_STATES

NOT_MERGEABLE = ("covered", "archived", "declined")


def dedupe_key(c: Candidate) -> str:
    raw = f"{c.venue}|{c.trader_key}|{c.coin}|{c.episode_id}|{c.event_class}|{c.level}"
    return hashlib.sha256(raw.encode()).hexdigest()


def add_evidence(s: Session, event_id: int, kind: str, ref_id: int, content_hash: str | None = None) -> None:
    s.execute(text("""INSERT INTO event_evidence (news_event_id, kind, ref_id, content_hash) VALUES (:e, :k, :r, :h)
                      ON CONFLICT DO NOTHING"""), {"e": event_id, "k": kind, "r": ref_id, "h": content_hash})


def link_story(s: Session, event_id: int, trader_id: int, trader_key: str, event_class: str) -> int:
    """Phase 1 stories: one saga per trader; every event of that trader is a chapter in order."""
    sid = s.execute(text("""
        INSERT INTO news_story (story_key, kind, trader_id) VALUES (:k, 'trader_saga', :t)
        ON CONFLICT (story_key) DO UPDATE SET last_event_at = now(), status = 'active' RETURNING id"""),
        {"k": f"trader:{trader_key}", "t": trader_id}).scalar_one()
    s.execute(text("SELECT id FROM news_story WHERE id = :s FOR UPDATE"), {"s": sid})
    seq = s.execute(text("SELECT coalesce(max(seq), 0) + 1 FROM story_event WHERE story_id = :s"), {"s": sid}).scalar()
    role = "origin" if seq == 1 else ("resolution" if event_class in ("whale_close", "liquidation") else "follow_up")
    s.execute(text("""INSERT INTO story_event (story_id, news_event_id, seq, role) VALUES (:s, :e, :q, :r)
                      ON CONFLICT DO NOTHING"""), {"s": sid, "e": event_id, "q": seq, "r": role})
    s.execute(text("UPDATE news_event SET story_id = :s WHERE id = :e"), {"s": sid, "e": event_id})
    return sid


def upsert(s: Session, c: Candidate) -> tuple[int, str] | None:
    """(event id, 'created' | 'merged' | 'exists'), or None when the candidate adds nothing new."""
    if c.event_class in MERGING:
        prev = s.execute(text("""SELECT id, state, escalation_level FROM news_event
                                 WHERE episode_id = :ep AND event_class = :c ORDER BY id DESC LIMIT 1 FOR UPDATE"""),
                         {"ep": c.episode_id, "c": c.event_class}).mappings().first()
        if prev and prev["state"] not in NOT_MERGEABLE:
            s.execute(text("""UPDATE news_event SET last_source_event_id = :se, source_event_count = source_event_count + 1,
                                     escalation_level = greatest(escalation_level, :lv), facts_refreshed_at = NULL,
                                     updated_at = now() WHERE id = :i"""),
                      {"se": c.source_event_id, "lv": c.level, "i": prev["id"]})
            if c.source_event_id:
                add_evidence(s, prev["id"], "source_event", c.source_event_id)
            return prev["id"], "merged"
        if prev:
            top = s.execute(text("""SELECT max(escalation_level) FROM news_event WHERE episode_id = :ep
                                    AND event_class = :c"""), {"ep": c.episode_id, "c": c.event_class}).scalar()
            if c.level <= top:
                return None
    eid = s.execute(text("""
        INSERT INTO news_event (dedupe_key, event_class, rule_version, trader_id, episode_id, venue, coin, direction,
                                escalation_level, liquidation_status, first_source_event_id, last_source_event_id)
        VALUES (:k, :c, :rv, :t, :ep, :v, :coin, :d, :lv, :liq, :se, :se)
        ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"""),
        {"k": dedupe_key(c), "c": c.event_class, "rv": RULE_VERSION, "t": c.trader_id, "ep": c.episode_id,
         "v": c.venue, "coin": c.coin, "d": c.direction, "lv": c.level, "liq": c.liquidation_status,
         "se": c.source_event_id}).scalar()
    if eid is None:
        eid = s.execute(text("SELECT id FROM news_event WHERE dedupe_key = :k"), {"k": dedupe_key(c)}).scalar_one()
        if c.source_event_id:
            add_evidence(s, eid, "source_event", c.source_event_id)
        return eid, "exists"
    s.execute(text("""INSERT INTO event_state_history (news_event_id, from_state, to_state, actor_kind, reason)
                      VALUES (:e, NULL, 'detected', 'system', :r)"""), {"e": eid, "r": f"rule {RULE_VERSION}"})
    if c.source_event_id:
        add_evidence(s, eid, "source_event", c.source_event_id)
    link_story(s, eid, c.trader_id, c.trader_key, c.event_class)
    return eid, "created"


def open_events_for_episode(s: Session, episode_id: int) -> list[int]:
    return list(s.execute(text("SELECT id FROM news_event WHERE episode_id = :e AND state = ANY(:st)"),
                          {"e": episode_id, "st": list(OPEN_STATES)}).scalars())
