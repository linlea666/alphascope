"""Anonymous public exchange observations. Smart Signal membership can change daily."""

import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic

import httpx
from sqlalchemy import text

from app.research import funding_history

SMART = "https://www.binance.com/bapi/futures/v1/public/future/smart-money/signal"
FUTURES = "https://fapi.binance.com"
OKX = "https://www.okx.com/api/v5/rubik/stat/contracts"
WINDOWS = ("30m", "1h", "24h", "7D")
RATIOS = {
    "accounts": ("globalLongShortAccountRatio", "long-short-account-ratio-contract"),
    "top_accounts": ("topLongShortAccountRatio", "long-short-account-ratio-contract-top-trader"),
    "top_positions": ("topLongShortPositionRatio", "long-short-position-ratio-contract-top-trader"),
}
STALE = timedelta(minutes=10)


def retry_time(header):
    now = datetime.now(UTC)
    minimum = now + timedelta(hours=1)
    try:
        if header and header.isdigit():
            return max(minimum, now + timedelta(seconds=int(header)))
        return max(minimum, parsedate_to_datetime(header))
    except (ValueError, OverflowError, TypeError):
        return minimum


def number(value):
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite exchange value")
    return result


def timestamp(ms):
    return datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def smart_data(response):
    if response.get("code") != "000000" or not response.get("success"):
        raise ValueError("Smart Signal did not return success")
    return response["data"]


def share(long, short):
    return long / (long + short) if long + short else None


def stats(raw):
    groups = {}
    for name, trader, amount in (("traders", "Traders", "Positions"), ("whales", "Whales", "WhalePositions")):
        sides = []
        for side in ("long", "short"):
            count = int(raw[side + trader])
            usd = number(raw[side + amount])
            qty = number(raw[side + amount.replace("Positions", "Qty")])
            if min(count, usd, qty) < 0:
                raise ValueError("negative gross statistic")
            sides.append({"n": count, "usd": usd, "qty": qty})
        long, short = sides
        groups[name] = {
            "long": long,
            "short": short,
            "head_share": share(long["n"], short["n"]),
            "money_share": share(long["usd"], short["usd"]),
            "net": long["usd"] - short["usd"],
        }
    return groups


def roster(raw, symbol):
    if raw["symbol"] != symbol:
        raise ValueError("unexpected instrument")
    return {
        "updated": timestamp(raw["updateTime"]),
        "total": int(raw["totalTraders"]),
        "value": number(raw["totalPositions"]),
        "ratio": number(raw.get("longShortRatio")),
        "entry": {group: {side: number(raw.get(f"{side}{field}AvgEntryPrice")) for side in ("long", "short")}
                  for group, field in (("traders", "Traders"), ("whales", "Whales"))},
    }


def fee(raw, symbol):
    if raw["symbol"] != symbol:
        raise ValueError("unexpected instrument")
    rate = number(raw["lastFundingRate"])
    if rate is None:
        raise ValueError("missing funding rate")
    return {
        "rate": rate,
        "payer": "long" if rate > 0 else "short" if rate < 0 else "none",
        "per_10000": abs(rate) * 10000,
        "due": timestamp(raw["nextFundingTime"]),
        "source_at": timestamp(raw["time"]),
    }


def okx_fee(raw, coin):
    if raw.get("code") != "0":
        raise ValueError("OKX did not return success")
    row = raw["data"][0]
    instrument = coin + "-USDT-SWAP"
    if row["instId"] != instrument:
        raise ValueError("unexpected instrument")
    # OKX fundingTime is the upcoming settlement; nextFundingTime is the one after it.
    return fee(
        {
            "symbol": instrument,
            "lastFundingRate": row["fundingRate"],
            "nextFundingTime": row["fundingTime"],
            "time": row["ts"],
        },
        instrument,
    )


def ratio(raw, source, symbol):
    if source == "binance":
        row = raw[-1]
        if row["symbol"] != symbol:
            raise ValueError("unexpected instrument")
        ts, value = row["timestamp"], row["longShortRatio"]
    else:
        if raw.get("code") != "0":
            raise ValueError("OKX did not return success")
        ts, value = raw["data"][0]
    r = number(value)
    if r is None or r < 0:
        raise ValueError("invalid ratio")
    return {"ratio": r, "long_share": r / (1 + r), "source_at": timestamp(ts)}


def requests_for(coin):
    sym = coin + "USDT"
    yield "roster", SMART + "/overview", {"symbol": sym}, lambda j: roster(smart_data(j), sym)
    for w in ("ALL", *WINDOWS):
        yield w, SMART + "/details/stats", {"symbol": sym, "timeRange": w}, lambda j: stats(smart_data(j))
    yield "funding", FUTURES + "/fapi/v1/premiumIndex", {"symbol": sym}, lambda j: fee(j, sym)
    yield (
        "okx_funding",
        "https://www.okx.com/api/v5/public/funding-rate",
        {"instId": coin + "-USDT-SWAP"},
        lambda j: okx_fee(j, coin),
    )
    for key, (bn, ok) in RATIOS.items():
        yield (
            "binance_" + key,
            FUTURES + "/futures/data/" + bn,
            {"symbol": sym, "period": "5m", "limit": 1},
            lambda j: ratio(j, "binance", sym),
        )
        yield (
            "okx_" + key,
            OKX + "/" + ok,
            {"instId": coin + "-USDT-SWAP", "period": "5m", "limit": 1},
            lambda j: ratio(j, "okx", sym),
        )


def latest(s, coin, cutoff):
    return (
        s.execute(
            text("""SELECT * FROM exchange_observation WHERE coin=:c AND published_at<=:t
                          ORDER BY bucket DESC LIMIT 1"""),
            {"c": coin, "t": cutoff},
        )
        .mappings()
        .first()
    )


def publish(s, coin, bucket, samples, errors):
    previous = s.scalar(
        text("""SELECT payload->'readings' FROM exchange_observation
        WHERE coin=:coin AND bucket<:bucket ORDER BY bucket DESC LIMIT 1"""),
        {"coin": coin, "bucket": bucket},
    )
    readings = {**(previous or {}), **samples}
    payload = {"symbol": coin + "USDT", "quote": "USDT", "version": 1, "readings": readings, "errors": errors}
    s.execute(
        text("""INSERT INTO exchange_observation(coin,bucket,payload) VALUES(:c,:b,CAST(:p AS jsonb))
                      ON CONFLICT(coin,bucket) DO NOTHING"""),
        {"c": coin, "b": bucket, "p": json.dumps(payload)},
    )


async def collect(sf, coins):
    start = datetime.now(UTC)
    bucket = start.replace(minute=start.minute // 5 * 5, second=0, microsecond=0)
    gate = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=8) as client:

        async def one(spec):
            key, url, params, normalize = spec
            try:
                async with gate:
                    resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = normalize(resp.json())
                reading = {
                    "source": url,
                    "instrument": params.get("instId") or params["symbol"],
                    "window": params.get("timeRange", params.get("period", "current")),
                    "at": datetime.now(UTC).isoformat(),
                    "data": data,
                }
                return key, reading, None
            except httpx.HTTPStatusError as exc:
                return key, None, str(exc.response.status_code)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                return key, None, type(exc).__name__

        async def market(coin):
            results = await asyncio.gather(*(one(spec) for spec in requests_for(coin)))
            samples = {k: v for k, v, err in results if not err}
            errors = {k: err for k, v, err in results if err}
            await funding_history.attach(client, sf, coin, samples)
            with sf.begin() as s:
                publish(s, coin, bucket, samples, errors)
            return errors

        results = await asyncio.gather(*(market(c) for c in coins), return_exceptions=True)
    gaps = {c: type(r).__name__ if isinstance(r, BaseException) else r for c, r in zip(coins, results) if r}
    return {"status": "partial" if gaps else "success", "gaps": gaps}


def snapshot(row, at):
    if not row:
        return None
    payload = row["payload"]
    readings = {}
    for key, sample in payload["readings"].items():
        data = sample["data"]
        times = [datetime.fromisoformat(sample["at"])]
        if "source_at" in data:
            times.append(datetime.fromisoformat(data["source_at"]))
        stale = at - min(times) > STALE
        if key in ("funding", "okx_funding") and datetime.fromisoformat(data["due"]) <= at:
            stale = True
        readings[key] = {**sample, "stale": stale}
    return {
        "id": row["id"],
        "coin": row["coin"],
        "published_at": row["published_at"],
        "watching": row["watching"],
        **payload,
        "readings": readings,
    }


_cache = {}
_lock = Lock()


def details(coin, window, side, cohort, order, page):
    params = {
        "symbol": coin + "USDT",
        "timeRange": window,
        "sortingField": "TIME",
        "sortingOrder": order,
        "rows": 10,
        "page": page,
    }
    if side != "all":
        params["positionSide"] = side.upper()
    if cohort == "whales":
        params["showAnonymousWhales"] = "true"
    key = tuple(params.items())
    with _lock:
        item = _cache.get(key)
        if item and monotonic() < item[0]:
            return item[1]
    response = httpx.get(SMART + "/details/list", params=params, timeout=8)
    response.raise_for_status()
    body = response.json()
    rows = []
    for r in smart_data(body):
        expected_side = {"short": "SELL", "long": "BUY"}.get(side)
        if expected_side and r.get("side") != expected_side:
            raise ValueError("upstream side filter mismatch")
        if r["timeRange"] != window or r["side"] not in ("BUY", "SELL"):
            raise ValueError("unexpected signal row")
        rows.append(
            {
                "public_id": r.get("topTraderId"),
                "name": r.get("traderName"),
                "n": int(r["traders"]),
                "side": r["side"],
                "usd": number(r["currentPositions"] if window == "ALL" else r["netPositions"]),
                "qty": number(r.get("currentQty") if window == "ALL" else r.get("netQty")),
                "entry": number(r.get("avgEntryPrice")),
                "time": timestamp(r["lastTradeTime"]) if r.get("lastTradeTime") else None,
            }
        )
    result = {
        "coin": coin,
        "symbol": params["symbol"],
        "source": "binance_smart_signal",
        "window": window,
        "cohort": cohort,
        "unit": "USDT",
        "rows": rows,
        "total": int(body["total"]),
        "collected_at": datetime.now(UTC).isoformat(),
    }
    with _lock:
        if len(_cache) >= 256:
            del _cache[next(iter(_cache))]
        _cache[key] = (monotonic() + 60, result)
    return result
