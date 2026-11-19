"""Public derivatives context, timestamped independently of wallet observations."""

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


async def collect_context(hl, okx, coins):
    results = {(c, v): {"fields": {}, "missing": []} for c in coins for v in ("hyperliquid", "okx")}
    try:
        meta, rows = (await hl.info({"type": "metaAndAssetCtxs"})).data
        universe = {m["name"]: r for m, r in zip(meta["universe"], rows, strict=True)}
    except Exception as exc:
        log.warning("research HL asset context: %s", type(exc).__name__)
        universe = {}
    hl_collected_at = datetime.now(UTC).isoformat()
    for coin in coins:
        entry = results[coin, "hyperliquid"]
        r = universe.get(coin, {})
        entry["fields"] = {
            k: number(r.get(raw))
            for k, raw in (
                ("oi", "openInterest"),
                ("mark", "markPx"),
                ("index", "oraclePx"),
                ("funding", "funding"),
            )
        }
        entry.update(
            oi_unit=coin, funding_kind="current_hourly", index_kind="oracle", context_collected_at=hl_collected_at
        )
        try:
            book = (await hl.info({"type": "l2Book", "coin": coin})).data
            bids, asks = ([(float(p["px"]), float(p["sz"])) for p in side] for side in book["levels"])
            entry["fields"]["depth"] = depth(bids, asks, coin)
            entry["book_time_ms"] = book.get("time")
        except Exception as exc:
            log.warning("research HL book %s: %s", coin, type(exc).__name__)
        entry = results[coin, "okx"]
        inst = coin + "-USDT-SWAP"
        entry.update(oi_unit="contracts", funding_kind="exchange_interval", index_kind="spot_index")
        for field, path, params, key in (
            ("oi", "public/open-interest", {"instType": "SWAP", "instId": inst}, "oi"),
            ("funding", "public/funding-rate", {"instId": inst}, "fundingRate"),
            ("mark", "public/mark-price", {"instType": "SWAP", "instId": inst}, "markPx"),
            ("index", "market/index-tickers", {"instId": coin + "-USDT"}, "idxPx"),
            ("depth", "market/books", {"instId": inst, "sz": "20"}, None),
        ):
            try:
                response = await okx.get("/api/v5/" + path, params)
                raw = response[0]
                if key:
                    entry["fields"][field] = number(raw.get(key))
                else:
                    sides = [[(float(p[0]), float(p[1])) for p in raw[side]] for side in ("bids", "asks")]
                    entry["fields"][field] = depth(*sides, "contracts")
                entry[field + "_time_ms"] = raw.get("ts")
                entry[field + "_collected_at"] = datetime.now(UTC).isoformat()
                if field == "funding":
                    entry["funding_time_ms"] = raw.get("fundingTime")
                    entry["next_funding_time_ms"] = raw.get("nextFundingTime")
            except Exception as exc:
                log.warning("research OKX %s %s: %s", coin, field, type(exc).__name__)
    for entry in results.values():
        f = entry["fields"]
        f["basis_bps"] = (f["mark"] / f["index"] - 1) * 10000 if f.get("mark") and f.get("index") else None
        entry["missing"] = [k for k in ("oi", "funding", "mark", "index", "basis_bps", "depth") if f.get(k) is None]
    return results
