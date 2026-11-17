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
from app.okx.client import OkxClient
from app.okx.leaderboard import ensure_daily
from app.okx.sync import refresh_assets, refresh_instruments, resolve_profiles, sync_history
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

    def okx_client() -> OkxClient:
        return OkxClient(settings.okx_base_url, settings.okx_requests_per_minute, settings.okx_timeout_seconds,
                         settings.okx_max_retries)

    def okx_daily_job() -> None:
        async def go():
            async with okx_client() as c:
                return await ensure_daily(sf, settings, c)
        r = asyncio.run(go())
        if r["status"] == "ok":
            log.info("okx boards: %s", {k: v for k, v in r.items() if k != "status"})

    def okx_history_job() -> None:
        async def go():
            async with okx_client() as c:
                r = await sync_history(sf, settings, c)
                return r, c.usage.requests
        r, n = asyncio.run(go())
        log.info("okx history: traders=%s stored=%s completed=%s errors=%s requests=%s", r["traders"], r["stored"],
                 r["completed"], r["errors"], n)

    def okx_upkeep_job() -> None:
        async def go():
            async with okx_client() as c:
                out = {"profiles": await resolve_profiles(sf, c), "assets": await refresh_assets(sf, c)}
                out["instruments"] = await refresh_instruments(sf, c)
                return out
        log.info("okx upkeep: %s", asyncio.run(go()))

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
    # OKX: the daily board check is cheap until the local crawl time; history and upkeep share the OKX budget.
    sched.add_job(okx_daily_job, "interval", minutes=30, next_run_time=now + timedelta(minutes=1), id="okx_daily")
    sched.add_job(okx_history_job, "interval", minutes=5, next_run_time=now + timedelta(minutes=2), id="okx_history")
    sched.add_job(okx_upkeep_job, "interval", hours=1, next_run_time=now + timedelta(minutes=6), id="okx_upkeep")
    log.info("worker started: leaderboard every %dm, enrich %d wallets every %dm",
             settings.leaderboard_interval_minutes, settings.enrich_batch_size, settings.enrich_interval_minutes)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopped")


if __name__ == "__main__":
    main()
