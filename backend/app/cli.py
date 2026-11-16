"""Command line entry points.

  python -m app.cli ingest-leaderboard [--file path.json]
  python -m app.cli enrich [--limit N] [--address 0x...]
  python -m app.cli tiers
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db import session_factory
from app.ingest.leaderboard_ingest import LeaderboardInput, ingest_leaderboard
from app.ingest.portfolio_ingest import enrich_batch
from app.ingest.runs import mark_abandoned_runs
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
    args = p.parse_args()

    settings = get_settings()
    sf = session_factory()
    store = LocalRawStore(settings.raw_store_dir)
    mark_abandoned_runs(sf)
    if args.cmd == "ingest-leaderboard":
        given = LeaderboardInput(args.file.read_bytes()) if args.file else None
        result = ingest_leaderboard(sf, settings, store, given)
    elif args.cmd == "enrich":
        result = asyncio.run(enrich_batch(sf, settings, store, args.limit, args.address))
    else:
        with sf() as s:
            result = recompute_tiers(s, datetime.now(UTC))
            s.commit()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
