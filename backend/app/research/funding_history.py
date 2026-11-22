"""Compare a live USDT perpetual quote with settled funding in the prior 12 complete months."""
import asyncio
import csv
import io
import json
import logging
import math
from datetime import UTC, datetime, timedelta, timezone
from zipfile import BadZipFile, ZipFile

import httpx
from sqlalchemy import text

from app.sources.binance_bulk import BASE_URL, fetch_verified

log = logging.getLogger(__name__)
VENUES = {"binance": UTC, "okx": timezone(timedelta(hours=8))}
HOUR = 3_600_000
MIN_SAME_SIDE = 30


def months_before(at, venue):
    month = at.astimezone(VENUES[venue]).date().replace(day=1)
    months = []
    for _ in range(12):
        month = (month - timedelta(days=1)).replace(day=1)
        months.append(month)
    return months[::-1]


def parse_month(body, venue, coin, month):
    instrument = f"{coin}-USDT-SWAP"
    with ZipFile(io.BytesIO(body)) as archive:
        files = [f for f in archive.infolist() if f.filename.endswith(".csv")]
        if len(files) != 1 or files[0].file_size > 2_000_000:
            raise ValueError("invalid funding archive")
        with archive.open(files[0]) as stream:
            records = {}
            for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")):
                if venue == "okx":
                    if row["instrument_name"] != instrument:
                        raise ValueError("unexpected OKX instrument")
                    stamp, rate, hours = int(row["funding_time"]), float(row["funding_rate"]), None
                else:
                    stamp, rate = int(row["calc_time"]), float(row["last_funding_rate"])
                    hours = int(row["funding_interval_hours"])
                day = datetime.fromtimestamp(stamp / 1000, VENUES[venue]).date()
                if day.replace(day=1) != month or not math.isfinite(rate):
                    raise ValueError("invalid funding settlement")
                if stamp in records and records[stamp] != [stamp, rate, hours]:
                    raise ValueError("conflicting funding settlement")
                records[stamp] = [stamp, rate, hours]
    if not records:
        raise ValueError("empty funding archive")
    return sorted(records.values())


async def fetch_month(client, venue, coin, month):
    if venue == "binance":
        sym = coin + "USDT"
        path = f"monthly/fundingRate/{sym}/{sym}-fundingRate-{month:%Y-%m}.zip"
        result = await fetch_verified(client, path)
        if result is None:
            raise ValueError("unpublished Binance archive")
        body, _ = result
        url = f"{BASE_URL}/{path}"
    else:
        # Official OKX historical market data archive. Each family has its own monthly file.
        url = ("https://static.okx.com/cdn/okex/traderecords/swaprates/monthly/"
               f"{month:%Y%m}/{coin}-USDT-SWAP-fundingrates-{month:%Y-%m}.zip")
        response = await client.get(url)
        response.raise_for_status()
        body = response.content
    return url, parse_month(body, venue, coin, month)


async def refresh(sf, coins):
    now = datetime.now(UTC)
    failures = {}
    async with httpx.AsyncClient(timeout=15) as client:
        async def series(venue, coin):
            with sf() as session:
                existing = set(session.scalars(text(
                    "SELECT month FROM funding_archive_month WHERE venue=:v AND coin=:c AND month>=:m"
                ), {"v": venue, "c": coin, "m": months_before(now, venue)[0]}))
            for month in months_before(now, venue):
                if month in existing:
                    continue
                try:
                    source, rows = await fetch_month(client, venue, coin, month)
                except (httpx.HTTPError, ValueError, KeyError, BadZipFile, IndexError) as exc:
                    failures[f"{venue}/{coin}/{month}"] = type(exc).__name__
                    continue
                with sf.begin() as session:
                    session.execute(text("""INSERT INTO funding_archive_month(venue,coin,month,source,settlements)
                        VALUES(:v,:c,:m,:url,CAST(:rows AS jsonb)) ON CONFLICT DO NOTHING"""),
                        {"v": venue, "c": coin, "m": month, "url": source, "rows": json.dumps(rows)})
        await asyncio.gather(*(series(v, c) for v in VENUES for c in coins if c in ("BTC", "ETH")))
    return {"status": "partial" if failures else "success", "gaps": failures}


def interval_hours(delta_ms):
    hours = round(delta_ms / HOUR)
    return hours if hours in (1, 2, 4, 8) and abs(delta_ms - hours * HOUR) <= 60_000 else None


def compare(monthly, quote, venue, hours):
    at = datetime.fromisoformat(quote["source_at"])
    months = months_before(at, venue)
    end = at.astimezone(VENUES[venue]).date().replace(day=1)
    rate = quote["rate"]
    result = {"start": str(months[0]), "end": str(end - timedelta(days=1)), "months": len(monthly),
              "hours": hours, "n": 0, "same": 0, "percentile": None, "side_pct": None, "level": None}
    if not hours:
        return result
    rates = []
    previous = None
    covered_hours = 0
    for month in months:
        for stamp, value, declared in monthly.get(month, []):
            gap = interval_hours(stamp - previous) if previous is not None else None
            previous = stamp
            period = declared if declared is not None else gap
            if period in (1, 2, 4, 8):
                covered_hours += period
                if period == hours:
                    rates.append(value)
    same = [abs(x) for x in rates if (x > 0 and rate > 0) or (x < 0 and rate < 0)]
    result.update(n=len(rates), same=len(same))
    if len(monthly) != 12 or covered_hours < 0.95 * (end - months[0]).days * 24 or not rates or rate == 0:
        return result
    result["side_pct"] = 100 * len(same) / len(rates)
    if len(same) >= MIN_SAME_SIDE:
        magnitude = abs(rate)
        rank = 100 * (sum(x < magnitude for x in same) + 0.5 * same.count(magnitude)) / len(same)
        level = "mild" if rank < 50 else "moderate" if rank < 80 else "high" if rank < 95 else "very_high"
        result.update(percentile=round(rank, 1), level=level)
    return result


# One lookup per settlement cycle in the long-lived worker. Never assume an eight-hour contract.
_last_due = {}


async def current_interval(client, venue, coin, quote):
    due = datetime.fromisoformat(quote["due"])
    at = datetime.fromisoformat(quote["source_at"])
    key = venue, coin
    cached = _last_due.get(key)
    if cached and cached[0] == due:
        return cached[1]
    if due <= at:
        return None
    if venue == "binance":
        instrument = coin + "USDT"
        response = await client.get("https://fapi.binance.com/fapi/v1/fundingRate",
                                    params={"symbol": instrument, "limit": 2, "endTime": int(at.timestamp()*1000)})
    else:
        instrument = coin + "-USDT-SWAP"
        response = await client.get("https://www.okx.com/api/v5/public/funding-rate-history",
                                    params={"instId": instrument, "limit": 2})
    response.raise_for_status()
    data = response.json()
    rows = data if venue == "binance" else data["data"]
    last = max((int(r["fundingTime"]) for r in rows if r.get("symbol", r.get("instId")) == instrument
                and int(r["fundingTime"]) <= at.timestamp()*1000), default=None)
    if last is None:
        return None
    hours = interval_hours(due.timestamp()*1000 - last)
    if hours:
        _last_due[key] = due, hours
    return hours


async def attach(client, sf, coin, samples):
    # Context only: a failure here must never keep the live quote from being published.
    for venue, key in (("binance", "funding"), ("okx", "okx_funding")):
        if key not in samples:
            continue
        quote = samples[key]["data"]
        try:
            months = months_before(datetime.fromisoformat(quote["source_at"]), venue)
            with sf() as session:
                monthly = dict(session.execute(text("""SELECT month,settlements FROM funding_archive_month
                    WHERE venue=:v AND coin=:c AND month BETWEEN :first AND :last"""),
                    {"v": venue, "c": coin, "first": months[0], "last": months[-1]}).all())
            try:
                hours = await current_interval(client, venue, coin, quote)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                hours = None
            quote["history"] = compare(monthly, quote, venue, hours)
        except Exception as exc:
            log.warning("funding history %s/%s unavailable: %s", venue, coin, type(exc).__name__)
            quote["history"] = None
