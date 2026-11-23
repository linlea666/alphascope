"""newsroom-worker: feed sync, detection, fact building and the job queue. One process; AI calls run in their own
threads so a slow or failing model never delays sync or detection. Nothing here talks to an exchange."""

import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal

from apscheduler.schedulers.blocking import BlockingScheduler

from nr import maintenance
from nr.columns import schedule
from nr.config import get_settings
from nr.db import session_factory
from nr.detect.at_risk import poll_at_risk
from nr.detect.run import process_pending
from nr.facts.build import refresh_due
from nr.feed.client import FeedError, FeedUnavailable
from nr.feed.health import poll_health
from nr.feed.sync import ensure_source, sync_events
from nr.jobs.handlers import DETERMINISTIC, default_deps, run_pending
from nr.jobs.queue import AI_KINDS

log = logging.getLogger("newsroom.worker")
STOP = threading.Event()


def queue_loop(kinds: tuple[str, ...], name: str, idle_seconds: float = 5.0) -> None:
    sf, deps = session_factory(), default_deps()
    while not STOP.is_set():
        try:
            done = run_pending(sf, deps, kinds, max_jobs=20, worker=name)
        except Exception:  # noqa: BLE001 - keep the loop alive; the job row records the failure
            log.exception("queue loop %s", name)
            done = []
        if not done:
            STOP.wait(idle_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    st = get_settings()
    sf, deps = session_factory(), default_deps()
    with sf() as s:
        source_id = ensure_source(s, st.feed_base_url)
        s.commit()
    liq_tol = {"v": Decimal("0.005")}
    try:
        with deps.feed() as c:
            liq_tol["v"] = Decimal(c.meta()["rules"]["liq_tolerance"])
    except (FeedUnavailable, FeedError) as exc:
        log.warning("feed meta unavailable at start (%s); using default rules", exc)

    def now() -> datetime:
        return datetime.now(UTC)

    def sync_job() -> None:
        with deps.feed() as c:
            r = sync_events(sf, c, source_id, now(), max_pages=st.sync_max_pages, limit=st.sync_page_limit)
        d = process_pending(sf, now(), liq_tol=liq_tol["v"])
        if r.get("inserted") or r.get("error") or d["created"] or d["merged"]:
            log.info("sync: %s detect: %s", r, d)

    def replay_job() -> None:
        with deps.feed() as c:
            log.info("replay: %s", sync_events(sf, c, source_id, now(), mode="replay", replay_minutes=st.replay_minutes))

    def health_job() -> None:
        with deps.feed() as c:
            poll_health(sf, c, source_id, now())

    def at_risk_job() -> None:
        with deps.feed() as c:
            r = poll_at_risk(sf, c, now())
        if r.get("created") or r.get("error"):
            log.info("at-risk: %s", r)

    def refresh_job() -> None:
        n = refresh_due(sf, now())
        if n:
            log.info("refresh: queued %s events", n)

    def column_job() -> None:
        queued = schedule.tick(sf, now())
        if queued:
            log.info("columns queued: %s", queued)

    def maintenance_job() -> None:
        log.info("maintenance: %s", maintenance.run(sf, now()))

    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                             "misfire_grace_time": 120})
    sched.add_job(health_job, "interval", minutes=2, next_run_time=now())
    sched.add_job(sync_job, "interval", seconds=60, next_run_time=now())
    sched.add_job(replay_job, "interval", minutes=30)
    sched.add_job(at_risk_job, "interval", minutes=10, next_run_time=now())
    sched.add_job(refresh_job, "interval", minutes=15)
    sched.add_job(maintenance_job, "interval", hours=1, next_run_time=now())
    sched.add_job(column_job, "cron", minute=7)
    threads = [threading.Thread(target=queue_loop, args=(DETERMINISTIC, "facts"), daemon=True)]
    threads += [threading.Thread(target=queue_loop, args=(AI_KINDS, f"ai-{i}"), daemon=True)
                for i in range(max(1, st.ai_max_concurrency))]
    for t in threads:
        t.start()
    log.info("newsroom worker started: feed=%s", st.feed_base_url)
    try:
        sched.start()
    finally:
        STOP.set()


if __name__ == "__main__":
    main()
