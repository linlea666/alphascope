"""Background scheduler: hourly leaderboard check (conditional GET) + budgeted Tier B enrichment.

Run with: python -m app.worker
"""

import asyncio
import logging
import multiprocessing
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from app.config import get_settings
from app.db import session_factory
from app.ingest.binance_ingest import import_binance
from app.ingest.binance_metrics import sync_metrics
from app.ingest.fill_sync import sync_fills
from app.ingest.leaderboard_ingest import ingest_leaderboard
from app.ingest.market_ingest import record_candles
from app.ingest.portfolio_ingest import enrich_batch
from app.ingest.runs import mark_abandoned_runs
from app.alerts import digest, sentinel
from app.factors.copy_run import run_copy_analysis
from app.factors.copy_validation import persistence_current, refresh_persistence
from app.factors.run import run_styles
from app.market.collect import sweep_hyperliquid, sweep_okx, sync_watch
from app.market.stats import snapshot_hour
from app.okx.candles import sync_candles
from app.okx.client import OkxClient
from app.okx.leaderboard import ensure_daily
from app.okx.sync import refresh_assets, refresh_instruments, resolve_profiles, sync_history
from app.research.profile_directory import discover as discover_binance_profiles
from app.research.binance_profiles import poll as poll_binance_profiles
from app.research.exchange import collect as collect_exchange
from app.research.factor_lab import refresh as refresh_factor_lab
from app.research.funding_history import refresh as refresh_funding_history
from app.research.jobs import market_research, research_daily, tracked
from app.research.positions import rebuild_stale
from app.research.timing import run_timing
from app.research.swings import run_swings
from app.sources.hl_client import HyperliquidInfoClient, WeightBucket
from app.sources.raw_store import LocalRawStore

log = logging.getLogger("alphascope.worker")


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


# Batch computations that make no API calls. Each runs in a fresh process: the scheduler process otherwise keeps
# the high-water heap of the largest one (CPython rarely returns freed memory), and the next batch then pushes the
# service past its memory limit. An out-of-memory kill now ends only that child.
def _timing(sf, settings):
    r = tracked(sf, "timing", lambda: run_timing(sf))
    log.info("timing: wallets=%s scored=%s significant=%s", r["wallets"], r["scored"], r["significant_q10"])


def _okx_timing(sf, settings):
    r = tracked(sf, "okx_timing", lambda: run_timing(sf, venue="okx"))
    log.info("okx timing: traders=%s scored=%s significant=%s", r["wallets"], r["scored"], r["significant_q10"])


def _swings(sf, settings):
    r = run_swings(sf, settings)
    log.info("swings: legs=%s screened=%s candidates=%s", r.get("legs"), r.get("wallets_screened"),
             r.get("candidates"))


def _positions(sf, settings):
    r = rebuild_stale(sf)
    log.info("positions: wallets=%s cycles=%s breaks=%s", r["wallets"], r["cycles"], r["breaks"])


def _styles(sf, settings):
    r = tracked(sf, "styles", lambda: run_styles(sf))
    log.info("styles: traders=%s tags=%s", r["traders"], r["tags"])


def _copy_analysis(sf, settings):
    log.info("copy analysis: %s", tracked(sf, "copy_analysis", lambda: run_copy_analysis(sf)))


def _factor_lab(sf, settings):
    log.info("factor lab: %s", tracked(sf, "factor_lab", lambda: refresh_factor_lab(sf, settings.research_coins)))


def _copy_persistence(sf, settings):
    log.info("copy persistence: %s", tracked(sf, "copy_persistence", lambda: refresh_persistence(sf)))


BATCH = {"timing": _timing, "okx_timing": _okx_timing, "swings": _swings, "positions": _positions,
         "styles": _styles, "copy_analysis": _copy_analysis, "factor_lab": _factor_lab,
         "copy_persistence": _copy_persistence}


def _child(name: str) -> None:
    _logging()
    BATCH[name](session_factory(), get_settings())


def isolated(name: str):
    def run() -> None:
        proc = multiprocessing.get_context("spawn").Process(target=_child, args=(name,), name=f"worker-{name}")
        proc.start()
        proc.join()
        if proc.exitcode:
            # negative: killed by that signal (-9 is the kernel's out-of-memory killer)
            log.error("%s exited with %s", name, proc.exitcode)
    run.__name__ = f"{name}_job"
    return run


def finished_within(sf, kind: str, hours: float) -> bool:
    with sf() as s:
        return bool(s.scalar(text("""SELECT EXISTS(SELECT 1 FROM ingest_run WHERE kind=:k
            AND status NOT IN ('running', 'failed', 'abandoned') AND finished_at > now() - make_interval(secs => :s))"""),
            {"k": kind, "s": hours * 3600}))


def main() -> None:
    _logging()
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


    def fills_job() -> None:
        r = asyncio.run(sync_fills(sf, settings, store))
        log.info("fills: %s selected=%s inserted=%s weight=%s", r["status"], r["selected"], r["inserted"],
                 r["api"]["weight"])

    def binance_job() -> None:
        r = asyncio.run(import_binance(sf, settings))
        log.info("binance: %s files=%s rows=%s missing=%s", r["status"], r["files"], r["rows"], r["missing"])




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

    def okx_candles_job() -> None:
        async def go():
            async with okx_client() as c:
                return await sync_candles(sf, c, max_requests=300)
        r = asyncio.run(go())
        log.info("okx candles: coins=%s bars=%s complete=%s", r["coins"], r["bars"], r["complete"])

    # A continuous stream of weight-2 wallet polls can otherwise starve the weight-20 market context request.
    research_rate = min(30.0, settings.hl_market_weight_per_minute * .1)
    radar_rate = settings.hl_market_weight_per_minute - research_rate
    research_bucket = WeightBucket(research_rate, burst=24)
    radar_bucket = WeightBucket(radar_rate, burst=2)

    def market_watch_job() -> None:
        with sf() as s:
            r = sync_watch(s, datetime.now(UTC))
            s.commit()
        log.info("market watch: %s", r)

    def market_hl_job() -> None:
        async def go():
            async with HyperliquidInfoClient(settings.hl_info_url, settings.hl_market_weight_per_minute,
                                             settings.hl_timeout_seconds, settings.hl_max_retries,
                                             bucket=radar_bucket) as c:
                # what the budget allows per run (2 weight per wallet, 10% kept for allMids and retries)
                limit = int(settings.market_sweep_interval_minutes * radar_rate / 2 * 0.9)
                return await sweep_hyperliquid(sf, c, limit=limit), c.usage.weight
        r, w = asyncio.run(go())
        log.info("market hl: due=%s ok=%s failed=%s events=%s weight=%s", r["due"], r["ok"], r["failed"],
                 r["events"], w)

    def market_okx_job() -> None:
        async def go():
            async with okx_client() as c:
                return await sweep_okx(sf, c)
        r = asyncio.run(go())
        log.info("market okx: due=%s ok=%s failed=%s events=%s", r["due"], r["ok"], r["failed"], r["events"])


    def market_snapshot_job() -> None:
        with sf() as s:
            n = snapshot_hour(s, datetime.now(UTC))
            s.commit()
        log.info("market snapshot: %s rows", n)

    def funding_history_job() -> None:
        tracked(sf, "funding_history", lambda: asyncio.run(refresh_funding_history(sf, settings.research_coins)))

    def binance_metrics_job() -> None:
        start = date.fromisoformat(settings.binance_core_start)
        tracked(sf, "binance_metrics", lambda: asyncio.run(sync_metrics(sf, settings.research_coins, start)))

    def exchange_job() -> None:
        tracked(sf, "exchange_observations", lambda: asyncio.run(collect_exchange(sf, settings.research_coins)))

    def research_job() -> None:
        async def collect():
            async with HyperliquidInfoClient(settings.hl_info_url, settings.hl_market_weight_per_minute,
                                             settings.hl_timeout_seconds, settings.hl_max_retries,
                                             bucket=research_bucket) as hl, okx_client() as okx:
                return await market_research(sf, settings, hl, okx)
        tracked(sf, "research_frames", lambda: asyncio.run(collect()))

    def research_catalog_job() -> None:
        tracked(sf, "research_catalog", lambda: research_daily(sf, settings))

    def sentinel_job() -> None:
        log.info("sentinels: %s", sentinel.run(sf))

    def digest_job() -> None:
        log.info("digest: %s", digest.run(sf))

    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                             "misfire_grace_time": 300})
    now = datetime.now(UTC)

    def catch_up(kind: str, hours: float, delay: timedelta) -> dict:
        # Batch jobs keep fixed clock times, so a restart neither shifts them nor runs them again. The first run
        # comes early (under the same job id, so it cannot overlap the regular one) only if a period was missed.
        return {} if finished_within(sf, kind, hours) else {"next_run_time": now + delay}

    sched.add_job(leaderboard_job, "interval", minutes=settings.leaderboard_interval_minutes,
                  next_run_time=now, id="leaderboard")
    sched.add_job(enrich_job, "interval", minutes=settings.enrich_interval_minutes,
                  next_run_time=now + timedelta(minutes=2), id="enrich")
    sched.add_job(market_job, "interval", minutes=settings.market_interval_minutes,
                  next_run_time=now + timedelta(minutes=1), id="market")
    # Swing screening needs fresh candles and portfolio points; it makes no API calls.
    sched.add_job(isolated("swings"), "cron", hour="*/6", minute=5, id="swings")
    sched.add_job(fills_job, "interval", minutes=settings.fill_interval_minutes,
                  next_run_time=now + timedelta(minutes=3), id="fills")
    # Binance daily archive files appear the day after; the job only fetches what is missing.
    sched.add_job(binance_job, "interval", hours=settings.binance_interval_hours,
                  next_run_time=now + timedelta(minutes=7), id="binance")
    sched.add_job(isolated("positions"), "interval", minutes=10, next_run_time=now + timedelta(minutes=4),
                  id="positions")
    # Timing scores depend on new fills, candles and pivots; recomputing every few hours is enough.
    sched.add_job(isolated("timing"), "cron", hour="*/3", minute=15, id="timing",
                  **catch_up("timing", 3, timedelta(minutes=15)))
    # OKX: ~2k traders with 1-2 years of positions (~20 min); their history changes hourly at most
    sched.add_job(isolated("okx_timing"), "cron", hour=2, minute=30, id="okx_timing",
                  **catch_up("okx_timing", 24, timedelta(minutes=40)))
    # OKX: the daily board check is cheap until the local crawl time; history and upkeep share the OKX budget.
    sched.add_job(okx_daily_job, "interval", minutes=30, next_run_time=now + timedelta(minutes=1), id="okx_daily")
    sched.add_job(okx_history_job, "interval", minutes=5, next_run_time=now + timedelta(minutes=2), id="okx_history")
    sched.add_job(okx_upkeep_job, "interval", hours=1, next_run_time=now + timedelta(minutes=6), id="okx_upkeep")
    sched.add_job(okx_candles_job, "interval", minutes=15, next_run_time=now + timedelta(minutes=8), id="okx_candles")
    # Market radar: who is watched (hourly), due-trader sweeps per venue, hourly aggregate snapshot at :55
    sched.add_job(market_watch_job, "interval", hours=1, next_run_time=now + timedelta(seconds=30), id="market_watch")
    sched.add_job(market_hl_job, "interval", minutes=settings.market_sweep_interval_minutes,
                  next_run_time=now + timedelta(minutes=1), id="market_hl")
    sched.add_job(market_okx_job, "interval", minutes=settings.market_sweep_interval_minutes,
                  next_run_time=now + timedelta(minutes=1, seconds=30), id="market_okx")
    sched.add_job(market_snapshot_job, "cron", minute=55, id="market_snapshot")
    sched.add_job(discover_binance_profiles, "interval", minutes=30, next_run_time=now + timedelta(seconds=20),
                  args=[sf], id="binance_profile_discovery")
    sched.add_job(poll_binance_profiles, "interval", minutes=1, args=[sf], id="binance_shared_profiles")
    sched.add_job(funding_history_job, "interval", hours=6, next_run_time=now + timedelta(seconds=5),
                  id="funding_history")
    sched.add_job(exchange_job, "cron", minute="*/5", second=10, id="exchange_observations",
                  next_run_time=now + timedelta(seconds=10))
    # Frames are only captured at :55. Forecasts settle against the frame nearest their due time (within five
    # minutes), so an off-schedule capture after a restart would take the hour's slot and leave the forecasts
    # issued an hour or a day earlier without an outcome. A restart therefore skips at most one hour.
    sched.add_job(research_job, "cron", minute=55, id="research_frames")
    sched.add_job(research_catalog_job, "interval", hours=6, next_run_time=now + timedelta(seconds=45),
                  id="research_catalog")
    # Factors and style labels (~20 s): after new cycles, timing scores and radar polls have landed
    sched.add_job(isolated("styles"), "cron", hour="0,6,12,18", minute=50, id="styles",
                  **catch_up("styles", 6, timedelta(seconds=90)))
    # Price-zone sentinels follow fill archiving; the digest goes out at 08:20 Beijing time
    sched.add_job(sentinel_job, "interval", minutes=30, next_run_time=now + timedelta(minutes=9), id="sentinels")
    sched.add_job(digest_job, "cron", hour=0, minute=20, id="digest")
    # Positioning history for the factor screen: each run backfills up to 300 daily files, then only new days.
    # Binance publishes a day's metrics file the next morning (UTC), so the screen runs after that.
    sched.add_job(binance_metrics_job, "cron", minute=35, id="binance_metrics", next_run_time=now + timedelta(minutes=3))
    sched.add_job(isolated("factor_lab"), "cron", hour=6, minute=40, id="factor_lab",
                  **catch_up("factor_lab", 24, timedelta(minutes=30)))
    # Does past copy return persist? Point-in-time replays over the whole archive, weekly (tens of thousands of
    # backtests, well clear of the nightly copy analysis).
    # A changed method supersedes the stored result, so it is recomputed after the deployment instead of on Sunday.
    sched.add_job(isolated("copy_persistence"), "cron", day_of_week="sun", hour=3, minute=30, id="copy_persistence",
                  **(catch_up("copy_persistence", 24 * 7, timedelta(minutes=45)) if persistence_current(sf)
                     else {"next_run_time": now + timedelta(minutes=45)}))
    # Copy backtests, copyability and trader health: nightly, and after a restart only if a night was missed
    sched.add_job(isolated("copy_analysis"), "cron", hour=1, minute=0, id="copy_analysis",
                  **catch_up("copy_analysis", 24, timedelta(minutes=20)))
    log.info("worker started: leaderboard every %dm, enrich %d wallets every %dm",
             settings.leaderboard_interval_minutes, settings.enrich_batch_size, settings.enrich_interval_minutes)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker stopped")


if __name__ == "__main__":
    main()
