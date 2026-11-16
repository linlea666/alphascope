"""Reconcile reconstructed positions with Hyperliquid's own view (clearinghouseState).

Fills are synced up to now first, so the last reconstructed position per coin must equal the live
position size. HIP-3 (`dex:coin`) positions live in separate clearinghouses and spot (`@n`) is not a
perp position, so those coins are reported as not comparable rather than as mismatches. A live position
in a coin without any archived fill was opened before the archive starts and never traded since; it is
reported as predates_archive (a coverage limit, not a reconstruction error).
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingest.fill_sync import sync_wallet
from app.research.positions import ZERO_TOL, current_positions, rebuild_wallet
from app.sources.hl_client import HyperliquidInfoClient
from app.sources.raw_store import RawStore


def _comparable(coin: str) -> bool:
    return ":" not in coin and not coin.startswith("@")


async def reconcile(sf: sessionmaker[Session], settings: Settings, store: RawStore, limit: int = 5) -> dict:
    with sf() as s:
        wallets = s.execute(text("""
            SELECT f.wallet_id, w.address, f.requested_start, f.latest_fill_at FROM fill_sync f
            JOIN wallet w ON w.id = f.wallet_id WHERE f.status = 'synced' AND f.n_fills > 0
            ORDER BY f.last_synced_at DESC LIMIT :n"""), {"n": limit}).all()
    budget = settings.hl_weight_per_minute * settings.hl_weight_safety
    report = []
    async with HyperliquidInfoClient(settings.hl_info_url, budget, settings.hl_timeout_seconds,
                                     settings.hl_max_retries) as client:
        for wid, address, start, latest in wallets:
            await sync_wallet(client, sf, store, None, wid, address, start, latest, settings.fill_max_pages)
            with sf() as s:
                rebuild_wallet(s, wid, datetime.now(UTC))
                s.commit()
                rebuilt = current_positions(s, wid)
                traded = set(s.execute(text("SELECT DISTINCT coin FROM trade_decision WHERE wallet_id = :w"),
                                       {"w": wid}).scalars())
            live_resp = await client.info({"type": "clearinghouseState", "user": address})
            live = {p["position"]["coin"]: Decimal(p["position"]["szi"])
                    for p in live_resp.data.get("assetPositions", [])}
            coins = sorted(set(rebuilt) | set(live))
            rows = []
            for c in coins:
                a, b = rebuilt.get(c, Decimal(0)), live.get(c, Decimal(0))
                if not _comparable(c):
                    status = "not_comparable"
                elif abs(a - b) <= ZERO_TOL:
                    status = "match"
                elif c not in traded:
                    status = "predates_archive"
                else:
                    status = "mismatch"
                rows.append({"coin": c, "rebuilt": str(a), "live": str(b), "status": status})
            report.append({"address": address, "positions": rows,
                           "match": sum(r["status"] == "match" for r in rows),
                           "mismatch": sum(r["status"] == "mismatch" for r in rows),
                           "predates_archive": sum(r["status"] == "predates_archive" for r in rows),
                           "not_comparable": sum(r["status"] == "not_comparable" for r in rows)})
    return {"wallets": len(report), "match": sum(r["match"] for r in report),
            "mismatch": sum(r["mismatch"] for r in report),
            "predates_archive": sum(r["predates_archive"] for r in report),
            "not_comparable": sum(r["not_comparable"] for r in report), "detail": report}
