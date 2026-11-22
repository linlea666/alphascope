import asyncio
import io
import json
from datetime import UTC, date, datetime, timedelta
from zipfile import ZipFile

import pytest
from sqlalchemy import text

from app.research import funding_history as fh

HOUR = 3_600_000


def archive(lines):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as z:
        z.writestr("rates.csv", "\n".join(lines))
    return buffer.getvalue()


def test_parse_both_archive_formats_and_reject_other_months():
    # Header and rows as published for 2026-10 (Binance stamps carry a few ms of calculation delay).
    rows = fh.parse_month(archive(["calc_time,funding_interval_hours,last_funding_rate",
                                   "1790812800001,8,0.00007981", "1790841600005,8,-0.00004568"]),
                          "binance", "BTC", date(2026, 10, 1))
    assert rows == [[1790812800001, 0.00007981, 8], [1790841600005, -0.00004568, 8]]
    # OKX months follow UTC+8: 2026-09-30 16:00 UTC is the first October settlement.
    okx = fh.parse_month(archive(["instrument_name,funding_rate,funding_time", "BTC-USDT-SWAP,0.0000367,1790784000000"]),
                         "okx", "BTC", date(2026, 10, 1))
    assert okx == [[1790784000000, 0.0000367, None]]
    with pytest.raises(ValueError):
        fh.parse_month(archive(["calc_time,funding_interval_hours,last_funding_rate", "1790784000002,8,0.0001"]),
                       "binance", "BTC", date(2026, 10, 1))
    with pytest.raises(ValueError):
        fh.parse_month(archive(["instrument_name,funding_rate,funding_time", "ETH-USDT-SWAP,0.0001,1790784000000"]),
                       "okx", "BTC", date(2026, 10, 1))


def test_months_are_complete_and_venue_local():
    at = datetime(2026, 11, 30, 20, tzinfo=UTC)  # already December 1st in UTC+8
    assert fh.months_before(at, "binance")[0] == date(2025, 11, 1)
    assert fh.months_before(at, "binance")[-1] == date(2026, 10, 1)
    assert fh.months_before(at, "okx")[-1] == date(2026, 11, 1)


def year(at, rate_of, hours=8):
    months = fh.months_before(at, "binance")
    start = int(datetime.combine(months[0], datetime.min.time(), UTC).timestamp() * 1000)
    end = int(datetime.combine(at.date().replace(day=1), datetime.min.time(), UTC).timestamp() * 1000)
    monthly = {}
    for i, stamp in enumerate(range(start, end, hours * HOUR)):
        month = datetime.fromtimestamp(stamp / 1000, UTC).date().replace(day=1)
        monthly.setdefault(month, []).append([stamp, rate_of(i), hours])
    return monthly


def test_percentile_uses_same_payer_and_interval():
    at = datetime(2026, 11, 21, 3, tzinfo=UTC)
    monthly = year(at, lambda i: (i % 100 + 1) * 1e-6 * (1 if i % 4 else -1))
    quote = {"source_at": at.isoformat(), "rate": 80.5e-6}
    result = fh.compare(monthly, quote, "binance", 8)
    assert result["months"] == 12 and result["n"] == result["same"] + sum(
        1 for rows in monthly.values() for r in rows if r[1] < 0)
    assert 70 < result["side_pct"] < 80
    assert 75 < result["percentile"] < 85 and result["level"] == "high"
    # A contract on another settlement interval has no comparable history.
    assert fh.compare(monthly, quote, "binance", 4)["percentile"] is None
    assert fh.compare(monthly, quote, "binance", None)["n"] == 0
    assert fh.compare(monthly, {**quote, "rate": 0.0}, "binance", 8)["side_pct"] is None


def test_missing_month_withholds_rating():
    at = datetime(2026, 11, 21, 3, tzinfo=UTC)
    monthly = year(at, lambda i: 1e-5)
    monthly.pop(min(monthly))
    result = fh.compare(monthly, {"source_at": at.isoformat(), "rate": 1e-5}, "binance", 8)
    assert result["months"] == 11 and result["percentile"] is None and result["level"] is None


def test_interval_from_spacing():
    assert fh.interval_hours(8 * HOUR + 5) == 8
    assert fh.interval_hours(4 * HOUR - 30_000) == 4
    assert fh.interval_hours(3 * HOUR) is None
    assert fh.interval_hours(8 * HOUR + 120_000) is None


class Broken:
    def __call__(self):
        raise RuntimeError("database unavailable")


def test_attach_never_blocks_the_live_quote(monkeypatch):
    async def interval(*args):
        return 8
    monkeypatch.setattr(fh, "current_interval", interval)
    at = datetime.now(UTC)
    samples = {"funding": {"data": {"source_at": at.isoformat(), "rate": 1e-4, "due": (at + timedelta(hours=1)).isoformat()}}}
    asyncio.run(fh.attach(None, Broken(), "BTC", samples))
    assert samples["funding"]["data"]["history"] is None and samples["funding"]["data"]["rate"] == 1e-4


def test_refresh_stores_each_month_once(sf, monkeypatch):
    calls = []

    async def fetch(client, venue, coin, month):
        calls.append((venue, coin, month))
        if venue == "okx" and month.month == 1:
            raise ValueError("unpublished")
        return f"https://example/{venue}/{month}", [[1, 1e-4, 8]]

    monkeypatch.setattr(fh, "fetch_month", fetch)
    first = asyncio.run(fh.refresh(sf, ["BTC", "SOL"]))
    assert first["status"] == "partial" and len(first["gaps"]) == 1
    assert {c for _, c, _ in calls} == {"BTC"} and len(calls) == 24
    calls.clear()
    asyncio.run(fh.refresh(sf, ["BTC"]))
    assert len(calls) == 1  # only the month that failed is retried
    with sf() as s:
        n, rows = s.execute(text("SELECT count(*), max(settlements::text) FROM funding_archive_month")).one()
    assert n == 23 and json.loads(rows) == [[1, 1e-4, 8]]
