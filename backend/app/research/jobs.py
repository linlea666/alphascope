"""Scheduled research publications; a failed venue or coin never rolls back another market."""

import logging
import resource
import sys
import time
from datetime import UTC, datetime

from app.models import IngestRun, ResearchFrame
from app.research.context import collect_context
from app.research.forward import evaluate, issue, register, settle
from app.research.frames import VENUES, capture
from app.research.replay import refresh_events

log = logging.getLogger(__name__)


def tracked(sf, kind, function):
    with sf.begin() as s:
        record = IngestRun(kind=kind, status="running", started_at=datetime.now(UTC), stats={})
        s.add(record)
        s.flush()
        rid = record.id
    start = time.monotonic()
    result, error = {}, None
    try:
        result = function()
        return result
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with sf.begin() as s:
            record = s.get(IngestRun, rid)
            record.status = "failed" if error else result.get("status", "success")
            record.finished_at = datetime.now(UTC)
            record.error = error
            record.stats = {
                **result,
                "duration_s": round(time.monotonic() - start, 2),
                "process_peak_mib": round(rss / (1024**2 if sys.platform == "darwin" else 1024), 1),
            }


async def market_research(sf, settings, hl, okx):
    contexts = await collect_context(hl, okx, settings.research_coins)
    failures, publications = [], 0
    for coin in settings.research_coins:
        for venue in VENUES:
            try:
                with sf.begin() as s:
                    s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                    fid = capture(s, coin, venue, datetime.now(UTC), contexts[coin, venue])
                if fid:
                    publications += 1
                with sf.begin() as s:
                    register(s, coin)
                    if venue == "hyperliquid" and fid:
                        issue(s, s.get(ResearchFrame, fid), datetime.now(UTC))
                    settle(s, coin, datetime.now(UTC))
                if contexts[coin, venue]["missing"]:
                    failures.append(f"{coin}/{venue}: " + ",".join(contexts[coin, venue]["missing"]))
            except Exception as exc:
                failures.append(f"{coin}/{venue}: {type(exc).__name__}")
                log.exception("market research for %s/%s", coin, venue)
    return {"status": "partial" if failures else "success", "frames": publications, "gaps": failures}


def research_daily(sf, settings):
    n = 0
    for coin in settings.research_coins:
        with sf.begin() as s:
            register(s, coin)
            n += refresh_events(s, coin, datetime.now(UTC))
    with sf.begin() as s:
        evaluate(s, datetime.now(UTC))
    return {"added_events": n}
