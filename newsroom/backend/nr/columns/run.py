"""Building a column issue: run the builder, store the issue as a news event with a versioned fact packet, apply the
gate, and hand it to the editor-in-chief like any other event. Issues are idempotent per (column, period, coin)."""

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import settings_store
from nr.columns.base import ColumnIssue
from nr.columns.defs import COLUMNS
from nr.events.service import link_story
from nr.events.states import InvalidTransition, transition
from nr.facts import builder
from nr.feed import health
from nr.feed.client import FeedClient
from nr.jobs.queue import enqueue

log = logging.getLogger(__name__)
COLUMN_SCORE = 50  # a scheduled column is worth an editor's look; the gate still decides if it can run


def dedupe_key(it: ColumnIssue) -> str:
    return hashlib.sha256(f"column|{it.column}|{it.period_key}|{it.coin}".encode()).hexdigest()


def packet(it: ColumnIssue, blocking: list[dict]) -> dict:
    return {
        "schema": builder.SCHEMA, "builder_version": "columns-1",
        "source": {"system": "alphascope", "feed_schema": "feed.v1", "column": it.column, "period": it.period_key},
        "subject": {"trader_ref": it.title_en, "trader_key": it.trader_key, "venue": issue_venue(it),
                    "identity": "unknown" if it.trader_key else "n/a", "title": {"zh": it.title_zh, "en": it.title_en}},
        "event": {"class": f"column:{it.column}", "coin": it.coin, "direction": None, "escalation_level": 0,
                  "liquidation_status": "none"},
        "facts": it.facts.items,
        "constraints": [{"code": c, "text": builder.CONSTRAINT_TEXT[c]} for c in sorted(it.facts.constraints)],
        "gate": {"status": "blocked" if blocking else "pass", "blocking": sorted({b["code"] for b in blocking})},
    }


def issue_venue(it: ColumnIssue) -> str:
    col = COLUMNS[it.column]
    if col.venue == "trader":
        return trader_venue(it.trader_key or "")
    return col.venue


def trader_venue(key: str) -> str:
    return "okx" if key.startswith("okx:") else "binance" if key.startswith("binance:") else "hyperliquid"


def store(s: Session, it: ColumnIssue, now: datetime) -> dict:
    venue = issue_venue(it)
    # a column only depends on its own source staying available: OKX or the relay never hold back Hyperliquid
    blocking = list(it.blocking) + health.source_blockers(s, venue if venue in ("okx", "binance") else "hyperliquid",
                                                          now, settings_store.get(s, "gate"))
    content = packet(it, blocking)
    trader_id = None
    if it.trader_key:
        trader_id = s.execute(text("""INSERT INTO trader (trader_key, venue) VALUES (:k, :v)
                                      ON CONFLICT (trader_key) DO UPDATE SET trader_key = EXCLUDED.trader_key
                                      RETURNING id"""),
                              {"k": it.trader_key, "v": trader_venue(it.trader_key)}
                              ).scalar_one()
    eid = s.execute(text("""
        INSERT INTO news_event (dedupe_key, event_class, rule_version, trader_id, venue, coin, column_key, period_key,
                                column_params, score, score_breakdown, score_version, priority)
        VALUES (:k, :c, 'col1', :t, :venue, :coin, :col, :period, CAST(:params AS jsonb), :score,
                CAST(:bd AS jsonb), 'col1', 'p2')
        ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"""),
        {"k": dedupe_key(it), "c": f"column:{it.column}", "t": trader_id, "venue": venue, "coin": it.coin, "col": it.column,
         "period": it.period_key, "params": json.dumps(it.params), "score": COLUMN_SCORE,
         "bd": json.dumps({"version": "col1", "total": COLUMN_SCORE, "components": {"scheduled_column": {
             "points": COLUMN_SCORE, "max": 100, "note": "scheduled editorial column", "inputs": {}}}})}).scalar()
    created = eid is not None
    if created:
        s.execute(text("""INSERT INTO event_state_history (news_event_id, from_state, to_state, actor_kind, reason)
                          VALUES (:e, NULL, 'detected', 'system', :r)"""), {"e": eid, "r": f"column {it.column}"})
        if trader_id:
            link_story(s, eid, trader_id, it.trader_key, f"column:{it.column}")
    else:
        eid = s.execute(text("SELECT id FROM news_event WHERE dedupe_key = :k"), {"k": dedupe_key(it)}).scalar_one()
    prev = s.execute(text("""SELECT p.id, p.version, p.content FROM news_event e JOIN fact_packet p
                             ON p.id = e.current_fact_packet_id WHERE e.id = :e"""), {"e": eid}).mappings().first()
    changed = builder.material_changes(prev["content"] if prev else None, content)
    if changed:
        version = (prev["version"] if prev else 0) + 1
        content["version"], content["event_id"] = version, eid
        pid = s.execute(text("""
            INSERT INTO fact_packet (news_event_id, version, builder_version, built_at, content, content_hash,
                                     supersedes_id, change_summary, gate_status, blocking_issues)
            VALUES (:e, :v, 'columns-1', :n, CAST(:c AS jsonb), :h, :sup, CAST(:cs AS jsonb), :g, CAST(:b AS jsonb))
            RETURNING id"""),
            {"e": eid, "v": version, "n": now, "c": builder.canonical(content), "h": builder.sha(content),
             "sup": prev["id"] if prev else None, "cs": json.dumps({"changed": changed[:50]}),
             "g": content["gate"]["status"], "b": json.dumps(blocking)}).scalar_one()
        s.execute(text("""UPDATE news_event SET current_fact_packet_id = :p, current_fact_version = :v WHERE id = :e"""),
                  {"p": pid, "v": version, "e": eid})
        from nr.articles.stale import on_new_packet
        on_new_packet(s, eid, prev["id"] if prev else None, pid, changed)
    gate = content["gate"]["status"]
    s.execute(text("""UPDATE news_event SET gate_status = :g, blocking_issues = CAST(:b AS jsonb), facts_refreshed_at = :n,
                             venue = :v, updated_at = now() WHERE id = :e"""),
              {"g": gate, "b": json.dumps(blocking), "n": now, "v": venue, "e": eid})
    state = s.execute(text("SELECT state FROM news_event WHERE id = :e"), {"e": eid}).scalar_one()
    target = ("blocked" if state in ("detected", "candidate", "evaluated", "assigned") else None) if gate == "blocked" \
        else ("candidate" if state in ("detected", "blocked") else None)
    if target and target != state:
        try:
            transition(s, eid, target, reason=f"column gate={gate}")
            state = target
        except InvalidTransition:
            pass
    ai = settings_store.get(s, "ai")
    if state == "candidate" and ai["enabled"] and ai["auto_eic"] and not settings_store.emergency_stopped(s):
        v = s.execute(text("SELECT current_fact_version FROM news_event WHERE id = :e"), {"e": eid}).scalar()
        enqueue(s, "eic_evaluate", {"event_id": eid}, dedupe_key=f"eic:{eid}:{v}", priority=70, now=now)
    return {"event_id": eid, "created": created, "gate": gate, "state": state, "changed": bool(changed)}


def build_column(sf: sessionmaker, client: FeedClient, key: str, now: datetime, params: dict | None = None) -> dict:
    col = COLUMNS[key]
    with sf() as s:
        issues = col.build(s, client, now, params or {})
        out = [store(s, it, now) for it in issues]
        s.commit()
    if out:
        log.info("column %s: %s", key, out)
    return {"column": key, "issues": out}
