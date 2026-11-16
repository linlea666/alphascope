"""Background scheduler: hourly leaderboard check (conditional GET) + budgeted Tier B enrichment.

Run with: python -m app.worker
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.db import session_factory
from app.ingest.binance_ingest import import_binance
from app.ingest.fill_sync import sync_fills
from app.ingest.leaderboard_ingest import ingest_leaderboard
from app.ingest.market_ingest import record_candles
from app.ingest.portfolio_ingest import enrich_batch
from app.ingest.runs import mark_abandoned_runs
from app.research.positions import rebuild_stale
from app.research.timing import run_timing
from app.research.swings import run_swings
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

    def market_job() -> None:
        # hourly housekeeping: runs left 'running' by a killed process (e.g. a restart) are closed out
        if n := mark_abandoned_runs(sf):
            log.warning("closed %d abandoned ingest runs", n)
        r = asyncio.run(record_candles(sf, settings))
        log.info("market: %s weight=%s", r["status"], r["api"]["weight"])

    def swings_job() -> None:
        r = run_swings(sf, settings)
        log.info("swings: legs=%s screened=%s candidates=%s", r.get("legs"), r.get("wallets_screened"),
                 r.get("candidates"))

    def fills_job() -> None:
        r = asyncio.run(sync_fills(sf, settings, store))
        log.info("fills: %s selected=%s inserted=%s weight=%s", r["status"], r["selected"], r["inserted"],
                 r["api"]["weight"])

    def binance_job() -> None:
        r = asyncio.run(import_binance(sf, settings))
        log.info("binance: %s files=%s rows=%s missing=%s", r["status"], r["files"], r["rows"], r["missing"])

    def positions_job() -> None:
        r = rebuild_stale(sf)
        log.info("positions: wallets=%s cycles=%s breaks=%s", r["wallets"], r["cycles"], r["breaks"])

    def timing_job() -> None:
        r = run_timing(sf)
        log.info("timing: wallets=%s scored=%s significant=%s", r["wallets"], r["scored"], r["significant_q10"])

    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                             "misfire_grace_time": 300})
    now = datetime.now(UTC)
    sched.add_job(leaderboard_job, "interval", minutes=settings.leaderboard_interval_minutes,
                  next_run_time=now, id="leaderboard")
    sched.add_job(enrich_job, "interval", minutes=settings.enrich_interval_minutes,
                  next_run_time=now + timedelta(minutes=2), id="enrich")
    sched.add_job(market_job, "interval", minutes=settings.market_interval_minutes,
                  next_run_time=now + timedelta(minutes=1), id="market")
    # Swing screening needs fresh candles and portfolio points; it is cheap (no API calls).
    sched.add_job(swings_job, "interval", hours=6, next_run_time=now + timedelta(minutes=5), id="swings")
    sched.add_job(fills_job, "interval", minutes=settings.fill_interval_minutes,
                  next_run_time=now + timedelta(minutes=3), id="fills")
    # Binance daily archive files appear the day after; the job only fetches what is missing.
    sched.add_job(binance_job, "interval", hours=settings.binance_interval_hours,
                  next_run_time=now + timedelta(minutes=7), id="binance")
    sched.add_job(positions_job, "interval", minutes=10, next_run_time=now + timedelta(minutes=4), id="positions")
    # Timing scores depend on new fills, candles and pivots; recomputing every few hours is enough.
    sched.add_job(timing_job, "interval", hours=3, next_run_time=now + timedelta(minutes=15), id="timing")
    log.info("worker started: leaderboard every %dm, enrich %d wallets every %dm",
             settings.leaderboard_interval_minutes, settings.enrich_batch_size, settings.enrich_interval_minutes)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopped")


if __name__ == "__main__":
    main()
