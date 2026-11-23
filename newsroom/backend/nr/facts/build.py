"""The build_facts job: fetch the trader's context, archive it as evidence, build the fact packet, version it only on
material change, apply the gate and move the event through its states."""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import settings_store
from nr.articles.stale import on_new_packet
from nr.events.service import add_evidence
from nr.events.states import InvalidTransition, transition
from nr.facts import builder
from nr.feed import health
from nr.feed.client import FeedClient, FeedError, FeedUnavailable
from nr.jobs.queue import enqueue

log = logging.getLogger(__name__)


class RateLimited(Exception):
    pass


def load_event(s: Session, event_id: int) -> dict:
    ev = dict(s.execute(text("""
        SELECT ne.*, t.trader_key, t.public_label FROM news_event ne JOIN trader t ON t.id = ne.trader_id
        WHERE ne.id = :i"""), {"i": event_id}).mappings().one())
    ev["story_published_before"] = bool(ev["story_id"] and s.execute(text("""
        SELECT 1 FROM publication_record pr JOIN article a ON a.id = pr.article_id
        WHERE a.story_id = :s AND a.news_event_id <> :e AND pr.status = 'published' LIMIT 1"""),
        {"s": ev["story_id"], "e": event_id}).scalar())
    return ev


def source_events(s: Session, event_id: int) -> list[dict]:
    return [dict(r) for r in s.execute(text("""
        SELECT se.* FROM event_evidence ev JOIN source_event se ON se.id = ev.ref_id
        WHERE ev.news_event_id = :e AND ev.kind = 'source_event' ORDER BY se.id"""), {"e": event_id}).mappings()]


def context_calls_last_hour(s: Session, now: datetime) -> int:
    return s.execute(text("""SELECT count(*) FROM job WHERE kind = 'build_facts' AND status = 'done'
                             AND finished_at > :t"""), {"t": now - timedelta(hours=1)}).scalar()


def snapshot_context(s: Session, trader_id: int, ctx: dict, now: datetime) -> int:
    body = {k: v for k, v in ctx.items() if k != "as_of"}
    h = builder.sha(body)
    s.execute(text("""INSERT INTO trader_context_snapshot (trader_id, fetched_at, feed_as_of, payload, payload_hash)
                      VALUES (:t, :n, :a, CAST(:p AS jsonb), :h) ON CONFLICT (trader_id, payload_hash) DO NOTHING"""),
              {"t": trader_id, "n": now, "a": ctx["as_of"], "p": json.dumps(ctx), "h": h})
    return s.execute(text("SELECT id FROM trader_context_snapshot WHERE trader_id = :t AND payload_hash = :h"),
                     {"t": trader_id, "h": h}).scalar_one()


def build_event_facts(sf: sessionmaker, client: FeedClient, event_id: int, now: datetime, cfg_limit: int = 240) -> dict:
    with sf() as s:
        if context_calls_last_hour(s, now) >= cfg_limit:
            raise RateLimited("context call budget for this hour is used up")
        ev = load_event(s, event_id)
        ep = dict(s.execute(text("SELECT * FROM position_episode WHERE id = :i"), {"i": ev["episode_id"]}).mappings().one())
        ses = source_events(s, event_id)
        blockers = health.source_blockers(s, ev["venue"], now, settings_store.get(s, "gate"))
    try:
        ctx = client.context(ev["trader_key"], coin=ev["coin"])
        ctx_error = None
    except (FeedUnavailable, FeedError) as exc:
        ctx, ctx_error = None, str(exc)
    with sf() as s:
        detect = settings_store.get(s, "detect")
        path = [dict(r) for r in s.execute(text("""SELECT kind, szi_after, observed_at FROM source_event
                                                     WHERE episode_id = :e ORDER BY id"""), {"e": ep["id"]}).mappings()]
        built = builder.build(ev, ep, ses, ctx, blockers, now, detect, path)
        if ctx is not None:
            sid = snapshot_context(s, ev["trader_id"], ctx, now)
            add_evidence(s, event_id, "context_snapshot", sid)
        hid = s.execute(text("SELECT max(id) FROM source_health_snapshot")).scalar()
        if hid:
            add_evidence(s, event_id, "health_snapshot", hid)
        prev = s.execute(text("""SELECT id, version, content FROM fact_packet WHERE id = :i"""),
                         {"i": ev["current_fact_packet_id"]}).mappings().first() if ev["current_fact_packet_id"] else None
        changed = builder.material_changes(prev["content"] if prev else None, built.content)
        new_id = None
        if changed:
            version = (prev["version"] if prev else 0) + 1
            built.content["version"] = version
            built.content["event_id"] = event_id
            new_id = s.execute(text("""
                INSERT INTO fact_packet (news_event_id, version, builder_version, built_at, content, content_hash,
                                         supersedes_id, change_summary, material, gate_status, blocking_issues)
                VALUES (:e, :v, :bv, :n, CAST(:c AS jsonb), :h, :sup, CAST(:cs AS jsonb), true, :g, CAST(:b AS jsonb))
                RETURNING id"""),
                {"e": event_id, "v": version, "bv": builder.BUILDER_VERSION, "n": now,
                 "c": builder.canonical(built.content), "h": builder.sha(built.content),
                 "sup": prev["id"] if prev else None, "cs": json.dumps({"changed": changed}), "g": built.gate_status,
                 "b": json.dumps(built.blocking)}).scalar_one()
            s.execute(text("""UPDATE news_event SET current_fact_packet_id = :p, current_fact_version = :v WHERE id = :e"""),
                      {"p": new_id, "v": version, "e": event_id})
            on_new_packet(s, event_id, prev["id"] if prev else None, new_id, changed)
        s.execute(text("""
            UPDATE news_event SET score = :sc, score_breakdown = CAST(:sb AS jsonb), score_version = :sv,
                   gate_status = :g, blocking_issues = CAST(:b AS jsonb), liquidation_status = :liq,
                   priority = coalesce(priority, :pr), facts_refreshed_at = :n, updated_at = now()
            WHERE id = :e"""),
            {"sc": built.score, "sb": json.dumps(built.breakdown), "sv": builder.SCORE_VERSION, "g": built.gate_status,
             "b": json.dumps(built.blocking), "liq": built.liquidation_status, "pr": priority(built.score), "n": now,
             "e": event_id})
        state = advance(s, ev, built, detect, now)
        s.commit()
    return {"event_id": event_id, "new_packet": new_id, "changed": changed[:10], "gate": built.gate_status,
            "score": float(built.score), "state": state, "context_error": ctx_error}


def priority(score) -> str:
    return "p0" if score >= 75 else "p1" if score >= 60 else "p2" if score >= 45 else "p3"


def advance(s: Session, ev: dict, built: builder.Built, detect: dict, now: datetime) -> str:
    """State after a (re)build. Blocked events wait for the data; low scores and unproven 'notable' traders are
    dismissed; nothing here ever publishes."""
    state = s.execute(text("SELECT state FROM news_event WHERE id = :i"), {"i": ev["id"]}).scalar_one()
    target = None
    if built.gate_status == "blocked":
        if state in ("detected", "candidate", "evaluating", "evaluated", "assigned"):
            target = "blocked"
    elif state in ("detected", "blocked", "dismissed"):
        if ev["event_class"] == "notable_trader_move" and not built.record_strong:
            target = "dismissed"
        elif built.score >= detect["candidate_score"]:
            target = "candidate"
        else:
            target = "dismissed"
    if target and target != state:
        try:
            transition(s, ev["id"], target, reason=f"gate={built.gate_status} score={built.score}"
                       + (f" blocking={[b['code'] for b in built.blocking]}" if built.blocking else ""))
            state = target
        except InvalidTransition:
            pass
    ai = settings_store.get(s, "ai")
    if (state == "candidate" and built.score >= detect["auto_eic_score"] and ai["enabled"] and ai["auto_eic"]
            and not settings_store.emergency_stopped(s)):
        version = s.execute(text("SELECT current_fact_version FROM news_event WHERE id = :i"), {"i": ev["id"]}).scalar()
        enqueue(s, "eic_evaluate", {"event_id": ev["id"]}, dedupe_key=f"eic:{ev['id']}:{version}", priority=60,
                now=now)
    return state


def refresh_due(sf: sessionmaker, now: datetime) -> int:
    """Re-check open events: merged changes, blocked events whose data may have recovered, liquidation confirmation."""
    with sf() as s:
        hours = settings_store.get(s, "detect")["refresh_hours"]
        ids = list(s.execute(text("""
            SELECT id FROM news_event
            WHERE (state IN ('detected', 'blocked', 'candidate', 'evaluated', 'assigned', 'covered')
                   OR (state = 'dismissed' AND event_class = 'liquidation' AND liquidation_status = 'suspected'))
              AND detected_at > :since AND column_key IS NULL
              AND (facts_refreshed_at IS NULL
                   OR facts_refreshed_at < CASE WHEN state = 'blocked' THEN :blocked ELSE :stale END
                   OR (event_class = 'liquidation' AND liquidation_status = 'suspected' AND facts_refreshed_at < :liq))
            ORDER BY facts_refreshed_at NULLS FIRST LIMIT 100"""),
            {"since": now - timedelta(hours=max(hours, 26)), "stale": now - timedelta(minutes=30),
             "blocked": now - timedelta(minutes=60),
             "liq": now - timedelta(hours=2)}).scalars())
        n = 0
        for i in ids:
            if enqueue(s, "build_facts", {"event_id": i}, dedupe_key=f"facts:{i}", priority=80, now=now):
                n += 1
        s.commit()
    return n
