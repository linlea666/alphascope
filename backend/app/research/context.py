"""Public derivatives context, timestamped independently of wallet observations."""

import asyncio
import logging
import math
from datetime import UTC, datetime

log = logging.getLogger(__name__)


def number(value):
    if value in ("", None):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def depth(bids, asks, size_unit):
    """Visible book depth within 50 bp, expressed in the source's native size unit."""
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    if mid <= 0 or bids[0][0] > asks[0][0]:
        return None
    return {
        "bid": sum(sz for px, sz in bids if px >= mid * 0.995),
        "ask": sum(sz for px, sz in asks if px <= mid * 1.005),
        "spread_bps": (asks[0][0] - bids[0][0]) / mid * 10000,
        "size_unit": size_unit,
        "band_bps": 50,
        "levels": [len(bids), len(asks)],
    }


async def hl_universe(hl):
    try:
        async with asyncio.timeout(60):
            meta, rows = (await hl.info({"type": "metaAndAssetCtxs"})).data
        universe = {m["name"]: r for m, r in zip(meta["universe"], rows, strict=True)}
    except Exception as exc:
        log.warning("research HL asset context: %s", type(exc).__name__)
        universe = {}
    return universe, datetime.now(UTC).isoformat()


def finish(entry):
    f = entry["fields"]
    f["basis_bps"] = (f["mark"] / f["index"] - 1) * 10000 if f.get("mark") and f.get("index") else None
    entry["missing"] = [k for k in ("oi", "funding", "mark", "index", "basis_bps", "depth") if f.get(k) is None]
    return entry


async def hl_context(hl, coin, universe_task):
    entry = {"fields": {}, "oi_unit": coin, "funding_kind": "current_hourly", "index_kind": "oracle"}
    try:
        async with asyncio.timeout(60):
            book = (await hl.info({"type": "l2Book", "coin": coin})).data
        bids, asks = ([(float(p["px"]), float(p["sz"])) for p in side] for side in book["levels"])
        entry["fields"]["depth"] = depth(bids, asks, coin)
        entry["book_time_ms"] = book.get("time")
        entry["book_collected_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        log.warning("research HL book %s: %s", coin, type(exc).__name__)
    universe, entry["context_collected_at"] = await universe_task
    row = universe.get(coin) or {}
    for field, key in (("oi", "openInterest"), ("mark", "markPx"), ("index", "oraclePx"), ("funding", "funding")):
        entry["fields"][field] = number(row.get(key))
    return coin, "hyperliquid", finish(entry), []


async def okx_context(okx, coin):
    inst = coin + "-USDT-SWAP"
    entry = {"fields": {}, "oi_unit": "contracts", "funding_kind": "exchange_interval", "index_kind": "spot_index"}
    candles = []

    async def read(field, path, params, key=None):
        try:
            async with asyncio.timeout(60):
                rows = await okx.get("/api/v5/" + path, params)
            fetched = datetime.now(UTC)
            if field == "candles":
                for r in rows:
                    if len(r) > 8 and str(r[8]) == "1":
                        candles.append(
                            {
                                "source": "okx",
                                "coin": coin,
                                "interval": "5m",
                                "t": datetime.fromtimestamp(int(r[0]) / 1000, UTC),
                                "o": r[1],
                                "h": r[2],
                                "l": r[3],
                                "c": r[4],
                                "v": r[5],
                                "n": None,
                                "f": fetched,
                            }
                        )
                return
            raw = rows[0]
            if key:
                entry["fields"][field] = number(raw.get(key))
            else:
                sides = [[(float(p[0]), float(p[1])) for p in raw[side]] for side in ("bids", "asks")]
                entry["fields"][field] = depth(*sides, "contracts")
            entry[field + "_time_ms"] = raw.get("ts")
            entry[field + "_collected_at"] = fetched.isoformat()
            if field == "funding":
                entry["funding_time_ms"] = raw.get("fundingTime")
                entry["next_funding_time_ms"] = raw.get("nextFundingTime")
        except Exception as exc:
            log.warning("research OKX %s %s: %s", coin, field, type(exc).__name__)

    await asyncio.gather(
        read("oi", "public/open-interest", {"instType": "SWAP", "instId": inst}, "oi"),
        read("funding", "public/funding-rate", {"instId": inst}, "fundingRate"),
        read("mark", "public/mark-price", {"instType": "SWAP", "instId": inst}, "markPx"),
        read("index", "market/index-tickers", {"instId": coin + "-USDT"}, "idxPx"),
        read("depth", "market/books", {"instId": inst, "sz": "20"}),
        read("candles", "market/candles", {"instId": inst, "bar": "5m", "limit": "36"}),
    )
    finish(entry)
    if not candles:
        entry["missing"].append("closed_5m_bars")
    return coin, "okx", entry, candles


async def collect_context(hl, okx, coins):
    """Yield each venue/asset when ready, even if a peer has timed out."""
    async with asyncio.TaskGroup() as tasks:
        universe = tasks.create_task(hl_universe(hl))
        markets = [
            task
            for coin in coins
            for task in (
                tasks.create_task(hl_context(hl, coin, universe)),
                tasks.create_task(okx_context(okx, coin)),
            )
        ]
        for ready in asyncio.as_completed(markets):
            yield await ready
