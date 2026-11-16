"""Command line entry points.

  python -m app.cli ingest-leaderboard [--file path.json]
  python -m app.cli enrich [--limit N] [--address 0x...]
  python -m app.cli tiers
  python -m app.cli market | swings | fills [--limit N]
  python -m app.cli binance [--max-files N] | positions | reconcile [--limit N] | timing
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
    sub.add_parser("timing", help="recompute pivots, decision timing and timing scores")
    args = p.parse_args()

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
        result = run_timing(sf)
    elif args.cmd == "reconcile":
        result = asyncio.run(reconcile(sf, settings, store, args.limit))
    elif args.cmd == "enrich":
        result = asyncio.run(enrich_batch(sf, settings, store, args.limit, args.address))
    else:
        with sf() as s:
            result = recompute_tiers(s, datetime.now(UTC))
            s.commit()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
