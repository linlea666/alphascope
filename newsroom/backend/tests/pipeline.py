from datetime import timedelta

from nr.detect.run import process_pending
from nr.feed.health import poll_health
from nr.feed.sync import ensure_source, sync_events
from nr.jobs.handlers import Deps, run_pending
from tests.fakes import T0


def setup_source(sf) -> int:
    with sf() as s:
        sid = ensure_source(s, "http://alphascope.test")
        s.commit()
    return sid


def pipeline(sf, feed, now=None) -> dict:
    """sync -> detect -> build facts, the way the worker chains them."""
    now = now or T0 + timedelta(hours=3)
    sid = setup_source(sf)
    with feed.client() as c:
        poll_health(sf, c, sid, now)
        sync = sync_events(sf, c, sid, now)
    det = process_pending(sf, now)
    jobs = run_pending(sf, Deps(feed=feed.client, now=lambda: now), ("build_facts",))
    return {"sync": sync, "detect": det, "jobs": jobs}
