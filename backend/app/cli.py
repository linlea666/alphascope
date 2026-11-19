"""Command line entry points.

  python -m app.cli ingest-leaderboard [--file path.json]
  python -m app.cli enrich [--limit N] [--address 0x...]
  python -m app.cli tiers
  python -m app.cli market | swings | fills [--limit N]
  python -m app.cli binance [--max-files N] | positions | reconcile [--limit N] | timing
  python -m app.cli okx-export --sqlite okx_rankings.sqlite3 --out bundle/ | okx-import --bundle bundle/ | okx-derive
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db import session_factory
from app.ingest.binance_ingest import import_binance
from app.ingest.fill_sync import sync_fills
from app.ingest.leaderboard_ingest import LeaderboardInput, ingest_leaderboard
from app.ingest.market_ingest import record_candles
from app.ingest.portfolio_ingest import enrich_batch
from app.ingest.runs import mark_abandoned_runs
from app.factors.run import run_styles
from app.okx.candles import sync_candles
from app.okx.client import OkxClient
from app.okx.legacy_import import derive_all, export_sqlite, import_bundle
from app.okx.leaderboard import ensure_daily, run_window
from app.okx.sync import refresh_assets, refresh_instruments, resolve_profiles, sync_history
from app.research.positions import rebuild_stale
from app.research.reconcile import reconcile
from app.research.timing import run_timing
from app.research.swings import run_swings
from app.sources.raw_store import LocalRawStore
from app.tiering import recompute_tiers


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="alphascope")
    sub = p.add_subparsers(dest="cmd", required=True)
    lb = sub.add_parser("ingest-leaderboard", help="fetch (or load) the official leaderboard and ingest it")
    lb.add_argument("--file", type=Path, help="ingest a previously downloaded real leaderboard JSON")
    en = sub.add_parser("enrich", help="fetch portfolio + clearinghouse for a batch of Tier B wallets")
    en.add_argument("--limit", type=int)
    en.add_argument("--address", action="append", help="enrich specific addresses (repeatable)")
    sub.add_parser("tiers", help="recompute wallet tiers")
    sub.add_parser("market", help="record candles for the reference coins")
    sub.add_parser("swings", help="recompute swing legs, per-wallet leg returns and fill candidates")
    fl = sub.add_parser("fills", help="archive fills for queued wallets")
    fl.add_argument("--limit", type=int)
    bn = sub.add_parser("binance", help="import Binance USDT-M klines from data.binance.vision")
    bn.add_argument("--max-files", type=int)
    sub.add_parser("positions", help="rebuild position cycles and decisions for wallets with new fills")
    rc = sub.add_parser("reconcile", help="sync fills, rebuild and compare positions with clearinghouseState")
    rc.add_argument("--limit", type=int, default=5)
    tm = sub.add_parser("timing", help="recompute pivots, decision timing and timing scores")
    tm.add_argument("--venue", choices=["hyperliquid", "okx"], default="hyperliquid")
    sub.add_parser("replay-copy-history", help="recompute legacy cached backtests at their original cutoffs")
    sub.add_parser("styles", help="recompute factors and style labels")
    ox = sub.add_parser("okx-export", help="export the old OKX collector's SQLite database to a JSONL bundle")
    ox.add_argument("--sqlite", type=Path, required=True)
    ox.add_argument("--out", type=Path, required=True)
    oi = sub.add_parser("okx-import", help="import an OKX export bundle (idempotent) and derive cycles")
    oi.add_argument("--bundle", type=Path, required=True)
    oi.add_argument("--no-derive", action="store_true")
    od = sub.add_parser("okx-derive", help="rebuild OKX cycles and decisions from stored positions")
    od.add_argument("--stale", action="store_true", help="only traders with new positions since the last derive")
    ob = sub.add_parser("okx-board", help="crawl an OKX leaderboard now (default: today's daily boards if due)")
    ob.add_argument("--window", choices=["7d", "30d", "90d", "365d"])
    oh = sub.add_parser("okx-history", help="sync OKX position history for due traders")
    oh.add_argument("--limit", type=int)
    sub.add_parser("okx-upkeep", help="resolve OKX profiles, refresh assets and contract specs")
    args = p.parse_args()
    if args.cmd == "okx-export":  # needs no database
        print(json.dumps(export_sqlite(args.sqlite, args.out), indent=2))
        return

    settings = get_settings()
    sf = session_factory()
    store = LocalRawStore(settings.raw_store_dir)
    mark_abandoned_runs(sf)
    if args.cmd == "ingest-leaderboard":
        given = LeaderboardInput(args.file.read_bytes()) if args.file else None
        result = ingest_leaderboard(sf, settings, store, given)
    elif args.cmd == "market":
        result = asyncio.run(record_candles(sf, settings))
    elif args.cmd == "swings":
        result = run_swings(sf, settings)
    elif args.cmd == "fills":
        result = asyncio.run(sync_fills(sf, settings, store, args.limit))
    elif args.cmd == "binance":
        result = asyncio.run(import_binance(sf, settings, args.max_files))
    elif args.cmd == "positions":
        result = rebuild_stale(sf)
    elif args.cmd == "timing":
        result = run_timing(sf, venue=args.venue)
    elif args.cmd == "reconcile":
        result = asyncio.run(reconcile(sf, settings, store, args.limit))
    elif args.cmd == "replay-copy-history":
        from app.factors.copy_run import replay_copy_history
        result=replay_copy_history(sf)
    elif args.cmd == "styles":
        result = run_styles(sf)
    elif args.cmd == "okx-import":
        result = import_bundle(sf, args.bundle, derive=not args.no_derive)
    elif args.cmd == "okx-derive":
        result = derive_all(sf, only_stale=args.stale)
    elif args.cmd in ("okx-board", "okx-history", "okx-upkeep"):
        result = asyncio.run(_okx(args, settings, sf))
    elif args.cmd == "enrich":
        result = asyncio.run(enrich_batch(sf, settings, store, args.limit, args.address))
    else:
        with sf() as s:
            result = recompute_tiers(s, datetime.now(UTC))
            s.commit()
    print(json.dumps(result, indent=2, default=str))


async def _okx(args, settings, sf) -> dict:
    async with OkxClient(settings.okx_base_url, settings.okx_requests_per_minute, settings.okx_timeout_seconds,
                         settings.okx_max_retries) as c:
        if args.cmd == "okx-board":
            if args.window:
                from app import settings_store
                with sf() as s:
                    tz = settings_store.get(s, "live").get("timezone") or "Asia/Shanghai"
                result = await run_window(sf, c, args.window, settings.okx_page_size, tz)
            else:
                result = await ensure_daily(sf, settings, c)
        elif args.cmd == "okx-history":
            result = await sync_history(sf, settings, c, args.limit)
        else:
            result = {"profiles": await resolve_profiles(sf, c), "assets": await refresh_assets(sf, c),
                      "instruments": await refresh_instruments(sf, c), "candles": await sync_candles(sf, c)}
        return {**result, "requests": c.usage.as_dict()}


if __name__ == "__main__":
    main()
