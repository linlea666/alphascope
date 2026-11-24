"""Source health: periodic snapshots of AlphaScope's feed health plus our own sync lag. These are inputs to the
credibility gate (nr/detect/gate.py): stale or unreachable data blocks publication, it never just lowers a score."""

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr.feed.client import FeedClient, FeedError, FeedUnavailable


def poll_health(sf: sessionmaker, client: FeedClient, source_id: int, now: datetime) -> dict:
    try:
        h = client.health()
        status = {"reachable": True, "radar": {v: h["sources"][f"radar_{v}"]["status"] for v in ("hyperliquid", "okx")},
                  "head_id": h["sources"]["market_event"]["head_id"]}
        payload, reachable = h, True
    except (FeedUnavailable, FeedError) as exc:
        status = {"reachable": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                  "radar": {"hyperliquid": "unknown", "okx": "unknown"}}
        payload, reachable = None, False
    with sf() as s:
        s.execute(text("""INSERT INTO source_health_snapshot (source_id, at, reachable, payload, status)
                          VALUES (:s, :n, :r, CAST(:p AS jsonb), CAST(:st AS jsonb))"""),
                  {"s": source_id, "n": now, "r": reachable, "p": json.dumps(payload, default=str) if payload else None,
                   "st": json.dumps(status)})
        s.commit()
    return status


def overview(s: Session, now: datetime) -> dict:
    """What the dashboard shows and the gate reads."""
    snap = s.execute(text("""SELECT at, reachable, status FROM source_health_snapshot ORDER BY at DESC, id DESC LIMIT 1""")
                     ).mappings().first()
    cp = s.execute(text("""SELECT after_id, head_id, updated_at FROM ingestion_checkpoint WHERE stream = 'events'
                           ORDER BY updated_at DESC, source_id DESC LIMIT 1""")).mappings().first()
    last_ok = s.execute(text("""SELECT max(finished_at) FROM sync_run WHERE status = 'success' AND mode = 'incremental'""")
                        ).scalar()
    last_run = s.execute(text("""SELECT status, error, finished_at FROM sync_run ORDER BY started_at DESC, id DESC LIMIT 1""")
                         ).mappings().first()
    return {
        "health_at": snap["at"] if snap else None, "reachable": bool(snap and snap["reachable"]),
        "radar": snap["status"]["radar"] if snap else {"hyperliquid": "unknown", "okx": "unknown"},
        "health_error": (snap["status"].get("error") if snap else None),
        "cursor": cp["after_id"] if cp else None, "head_id": cp["head_id"] if cp else None,
        "backlog": max((cp["head_id"] or 0) - cp["after_id"], 0) if cp and cp["head_id"] is not None else None,
        "last_sync_ok_at": last_ok, "last_sync": dict(last_run) if last_run else None, "as_of": now,
    }


def source_blockers(s: Session, venue: str, now: datetime, gate_cfg: dict) -> list[dict]:
    """Blocking issues that come from the source rather than from the event itself."""
    if venue == "binance":  # the kolcue relay: its sync must have succeeded recently
        ok = s.execute(text("SELECT last_ok_at FROM source_connection WHERE name = 'kolcue_relay'")).scalar()
        if ok is None or now - ok > timedelta(minutes=gate_cfg["max_sync_age_minutes"]):
            return [{"code": "SOURCE_STALE", "detail": f"relay last synced {ok}"}]
        return []
    o = overview(s, now)
    out = []
    if (not o["reachable"] or o["health_at"] is None
            or now - o["health_at"] > timedelta(minutes=gate_cfg["max_health_age_minutes"])):
        out.append({"code": "SOURCE_STALE", "detail": o["health_error"] or "no recent health check"})
    elif o["radar"].get(venue) not in ("ok", "lagging"):
        out.append({"code": "SOURCE_STALE", "detail": f"{venue} radar is {o['radar'].get(venue)}"})
    if (o["last_sync_ok_at"] is None or now - o["last_sync_ok_at"] > timedelta(minutes=gate_cfg["max_sync_age_minutes"])
            or (o["backlog"] or 0) > gate_cfg["max_backlog"]):
        out.append({"code": "SYNC_BACKLOG", "detail": f"backlog={o['backlog']} last_sync_ok_at={o['last_sync_ok_at']}"})
    return out
