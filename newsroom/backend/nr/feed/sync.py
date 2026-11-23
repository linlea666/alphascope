"""Incremental, idempotent copy of AlphaScope's radar events into source_event.

The cursor (ingestion_checkpoint) advances in the same transaction as the rows it covers, so a crash never skips or
double-counts. A periodic replay re-reads from an older cursor as a safety net; inserts are idempotent on
(source, source_event_id), so a replay can only add rows the incremental pass somehow missed (expected: none).
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr.feed.client import FeedClient, FeedError, FeedUnavailable

log = logging.getLogger(__name__)
SOURCE = "alphascope"
STREAM = "events"
HISTORY_EVERY = timedelta(minutes=10)


def ensure_source(s: Session, base_url: str) -> int:
    return s.execute(text("""
        INSERT INTO source_connection (name, base_url) VALUES (:n, :u)
        ON CONFLICT (name) DO UPDATE SET base_url = EXCLUDED.base_url RETURNING id"""),
        {"n": SOURCE, "u": base_url}).scalar_one()


def upsert_traders(s: Session, rows: list[dict]) -> dict[str, int]:
    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["trader_key"]] = r
    out = {}
    for key, r in seen.items():
        out[key] = s.execute(text("""
            INSERT INTO trader (trader_key, venue, display_name, handle) VALUES (:k, :v, :d, :h)
            ON CONFLICT (trader_key) DO UPDATE SET
              display_name = coalesce(EXCLUDED.display_name, trader.display_name),
              handle = coalesce(EXCLUDED.handle, trader.handle),
              updated_at = CASE WHEN trader.display_name IS DISTINCT FROM coalesce(EXCLUDED.display_name, trader.display_name)
                                  OR trader.handle IS DISTINCT FROM coalesce(EXCLUDED.handle, trader.handle)
                                THEN now() ELSE trader.updated_at END
            RETURNING id"""), {"k": key, "v": r["venue"], "d": r.get("display_name"), "h": r.get("handle")}).scalar_one()
    return out


def payload_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def insert_events(s: Session, source_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    traders = upsert_traders(s, rows)
    params = [{**r, "src": source_id, "tid": traders[r["trader_key"]], "h": payload_hash(r)} for r in rows]
    res = s.execute(text("""
        INSERT INTO source_event (source_id, source_event_id, trader_id, venue, coin, kind, side, szi_before, szi_after,
          notional_change, mark_px, entry_px, leverage, liq_px, event_time, time_precision, window_start, observed_at,
          payload_hash)
        VALUES (:src, :source_id, :tid, :venue, :coin, :kind, :side, :szi_before, :szi_after, :notional_change,
          :mark_px, :entry_px, :leverage, :liq_px, :event_time, :time_precision, :window_start, :observed_at, :h)
        ON CONFLICT (source_id, source_event_id) DO NOTHING"""), params)
    return res.rowcount


def _checkpoint(s: Session, source_id: int) -> dict:
    s.execute(text("""INSERT INTO ingestion_checkpoint (source_id, stream) VALUES (:s, :st) ON CONFLICT DO NOTHING"""),
              {"s": source_id, "st": STREAM})
    return dict(s.execute(text("""SELECT after_id, head_id, updated_at FROM ingestion_checkpoint
                                   WHERE source_id = :s AND stream = :st FOR UPDATE"""),
                          {"s": source_id, "st": STREAM}).mappings().one())


def _start_run(sf: sessionmaker, source_id: int, mode: str, from_id: int, now: datetime) -> int:
    with sf() as s:
        rid = s.execute(text("""INSERT INTO sync_run (source_id, stream, mode, started_at, status, from_id)
                                VALUES (:s, :st, :m, :n, 'running', :f) RETURNING id"""),
                        {"s": source_id, "st": STREAM, "m": mode, "n": now, "f": from_id}).scalar_one()
        s.commit()
    return rid


def _finish_run(sf: sessionmaker, rid: int, source_id: int, status: str, stats: dict, error: str | None,
                now: datetime) -> None:
    with sf() as s:
        s.execute(text("""UPDATE sync_run SET finished_at = :n, status = :st, to_id = :to, pages = :p, fetched = :f,
                                 inserted = :i, backlog = :b, error = :e WHERE id = :id"""),
                  {"n": now, "st": status, "to": stats["to_id"], "p": stats["pages"], "f": stats["fetched"],
                   "i": stats["inserted"], "b": stats["backlog"], "e": error, "id": rid})
        if error:
            s.execute(text("UPDATE source_connection SET last_error = :e, last_error_at = :n WHERE id = :id"),
                      {"e": error[:500], "n": now, "id": source_id})
        else:
            s.execute(text("UPDATE source_connection SET last_ok_at = :n WHERE id = :id"), {"n": now, "id": source_id})
        s.commit()


def sync_events(sf: sessionmaker, client: FeedClient, source_id: int, now: datetime, mode: str = "incremental",
                max_pages: int = 20, limit: int = 1000, replay_minutes: int = 60) -> dict:
    with sf() as s:
        cp = _checkpoint(s, source_id)
        s.commit()
    if mode == "replay":
        with sf() as s:
            start = s.execute(text("""
                SELECT after_id FROM ingestion_checkpoint_history
                WHERE source_id = :s AND stream = :st AND recorded_at <= :t ORDER BY recorded_at DESC LIMIT 1"""),
                {"s": source_id, "st": STREAM, "t": now - timedelta(minutes=replay_minutes)}).scalar()
        if start is None:
            return {"mode": mode, "skipped": "no checkpoint old enough"}
        stop_at = cp["after_id"]
    else:
        start, stop_at = cp["after_id"], None
    stats = {"pages": 0, "fetched": 0, "inserted": 0, "to_id": start, "backlog": None}
    rid = _start_run(sf, source_id, mode, start, now)
    cursor = start
    try:
        for _ in range(max_pages):
            page = client.events(after_id=cursor, limit=limit)
            rows = page["rows"]
            if stop_at is not None:
                rows = [r for r in rows if r["source_id"] <= stop_at]
            with sf() as s:
                inserted = insert_events(s, source_id, rows)
                nxt = page["next_after_id"]
                if mode == "incremental":
                    cur = _checkpoint(s, source_id)
                    if cur["after_id"] != cursor:  # another sync moved it: stop rather than race
                        s.rollback()
                        break
                    s.execute(text("""UPDATE ingestion_checkpoint SET after_id = :a, head_id = :h, settled_before = :sb,
                                             updated_at = :n WHERE source_id = :s AND stream = :st"""),
                              {"a": nxt, "h": page["head_id"], "sb": page["settled_before"], "n": now,
                               "s": source_id, "st": STREAM})
                    last = s.execute(text("""SELECT max(recorded_at) FROM ingestion_checkpoint_history
                                             WHERE source_id = :s AND stream = :st"""),
                                     {"s": source_id, "st": STREAM}).scalar()
                    if last is None or now - last >= HISTORY_EVERY:
                        s.execute(text("""INSERT INTO ingestion_checkpoint_history (source_id, stream, after_id, recorded_at)
                                          VALUES (:s, :st, :a, :n)"""),
                                  {"s": source_id, "st": STREAM, "a": nxt, "n": now})
                s.commit()
            stats["pages"] += 1
            stats["fetched"] += len(rows)
            stats["inserted"] += inserted
            stats["to_id"] = nxt
            stats["backlog"] = max((page["head_id"] or 0) - nxt, 0)
            cursor = nxt
            if not page["has_more"] or (stop_at is not None and cursor >= stop_at):
                break
    except (FeedUnavailable, FeedError) as exc:
        _finish_run(sf, rid, source_id, "failed", stats, f"{type(exc).__name__}: {exc}", now)
        log.warning("feed %s sync failed: %s", mode, exc)
        return {"mode": mode, "error": str(exc), **stats}
    _finish_run(sf, rid, source_id, "success", stats, None, now)
    if mode == "replay" and stats["inserted"]:
        log.warning("feed replay found %s rows the incremental sync missed", stats["inserted"])
    return {"mode": mode, **stats}
