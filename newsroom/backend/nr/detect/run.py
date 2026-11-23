"""Normalize newly synced source events into episodes and run the detection rules on each change."""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from nr import settings_store
from nr.detect import rules
from nr.events import service
from nr.jobs.queue import enqueue
from nr.normalize import episodes

log = logging.getLogger(__name__)
LIQ_TOLERANCE = Decimal("0.005")  # overridden by the feed's meta rules when known


def process_pending(sf: sessionmaker, now: datetime, batch: int = 2000, liq_tol: Decimal = LIQ_TOLERANCE) -> dict:
    out = {"events": 0, "created": 0, "merged": 0}
    with sf() as s:
        cfg = settings_store.get(s, "detect")
        for e in episodes.pending(s, batch):
            ch = episodes.apply(s, e)
            out["events"] += 1
            for c in rules.evaluate(ch, cfg, liq_tol):
                r = service.upsert(s, c)
                if r is None:
                    continue
                eid, how = r
                if how in ("created", "merged"):
                    out[how] += 1
                    enqueue(s, "build_facts", {"event_id": eid}, dedupe_key=f"facts:{eid}", priority=50, now=now)
        s.commit()
    if out["events"]:
        log.info("detect: %s", out)
    return out
