"""Background scheduler: hourly leaderboard check (conditional GET) + budgeted Tier B enrichment.

Run with: python -m app.worker
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.db import session_factory
from app.ingest.leaderboard_ingest import ingest_leaderboard
from app.ingest.portfolio_ingest import enrich_batch
from app.ingest.runs import mark_abandoned_runs
from app.sources.raw_store import LocalRawStore

log = logging.getLogger("alphascope.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    sf = session_factory()
    store = LocalRawStore(settings.raw_store_dir)
    if n := mark_abandoned_runs(sf):
        log.warning("closed %d abandoned ingest runs", n)

    def leaderboard_job() -> None:
        r = ingest_leaderboard(sf, settings, store)
        log.info("leaderboard: %s new=%s disappeared=%s", r["status"], r.get("new_wallets"),
                 r.get("disappeared_wallets"))

    def enrich_job() -> None:
        r = asyncio.run(enrich_batch(sf, settings, store))
        log.info("enrich: %s selected=%s ok=%s failed=%s weight=%s", r["status"], r["selected"], r["ok"],
                 r["failed"], r["api"]["weight"])

    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                             "misfire_grace_time": 300})
    now = datetime.now(UTC)
    sched.add_job(leaderboard_job, "interval", minutes=settings.leaderboard_interval_minutes,
                  next_run_time=now, id="leaderboard")
    sched.add_job(enrich_job, "interval", minutes=settings.enrich_interval_minutes,
                  next_run_time=now + timedelta(minutes=2), id="enrich")
    log.info("worker started: leaderboard every %dm, enrich %d wallets every %dm",
             settings.leaderboard_interval_minutes, settings.enrich_batch_size, settings.enrich_interval_minutes)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopped")


if __name__ == "__main__":
    main()
