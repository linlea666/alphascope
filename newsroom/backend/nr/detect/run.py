"""Normalize newly synced source events into episodes and run the detection rules on each change."""

import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.detect import rules
from nr.events import service
from nr.jobs.queue import enqueue
from nr.normalize import episodes

log = logging.getLogger(__name__)
LIQ_TOLERANCE = Decimal("0.005")  # overridden by the feed's meta rules when known


def process_pending(sf: sessionmaker, now: datetime, batch: int = 2000, liq_tol: Decimal = LIQ_TOLERANCE,
                    max_seconds: float = 45) -> dict:
    """Works through the backlog in committed batches until it is empty or the time budget is spent."""
    out = {"events": 0, "created": 0, "merged": 0, "too_old": 0}
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        with sf() as s:
            cfg = settings_store.get(s, "detect")
            oldest = now - timedelta(hours=cfg["max_event_age_hours"])
            pending = episodes.pending(s, batch)
            for e in pending:
                ch = episodes.apply(s, e)
                out["events"] += 1
                ended = ch.ended or (ch.episode if ch.episode["status"] != "open" else None)
                if ended is not None and s.execute(text(
                        "SELECT 1 FROM news_event WHERE episode_id = :e AND state IN ('covered', 'archived') LIMIT 1"),
                        {"e": ended["id"]}).scalar():
                    # a position we reported on has ended: its whole story becomes a recap candidate
                    enqueue(s, "column_build", {"column": "recap", "params": {"episode_id": ended["id"]}},
                            dedupe_key=f"column:recap:{ended['id']}", priority=60, now=now)
                if e["observed_at"] < oldest:
                    out["too_old"] += 1
                    continue
                for c in rules.evaluate(ch, cfg, liq_tol):
                    r = service.upsert(s, c)
                    if r is None:
                        continue
                    eid, how = r
                    if how in ("created", "merged"):
                        out[how] += 1
                        enqueue(s, "build_facts", {"event_id": eid}, dedupe_key=f"facts:{eid}", priority=50, now=now)
            s.commit()
        if len(pending) < batch:
            break
    if out["events"]:
        log.info("detect: %s", out)
    return out
