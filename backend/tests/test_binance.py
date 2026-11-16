import asyncio
import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
from sqlalchemy import text

from app.ingest.binance_ingest import aggregate, candidate_symbol, compare, plan_files, store_klines
from app.sources.binance_bulk import BASE_URL, ChecksumError, Kline, daily_path, fetch_verified, monthly_path, parse_klines

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def zip_csv(rows: list[str], header: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        lines = (["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
                  "taker_buy_quote_volume,ignore"] if header else []) + rows
        z.writestr("BTCUSDT-1m-2026-08-01.csv", "\n".join(lines))
    return buf.getvalue()


def row(minute: int, o, h, l, c, v=1, n=10, unit_ms=True) -> str:  # noqa: E741
    t = int((T0 + timedelta(minutes=minute)).timestamp() * 1000)
    if not unit_ms:
        t *= 1000
    return f"{t},{o},{h},{l},{c},{v},{t + 59_999},0,{n},0,0,0"


def test_parse_with_and_without_header_and_microseconds():
    a = parse_klines(zip_csv([row(0, 1, 2, 0.5, 1.5)]))
    b = parse_klines(zip_csv([row(0, 1, 2, 0.5, 1.5)], header=False))
    c = parse_klines(zip_csv([row(0, 1, 2, 0.5, 1.5, unit_ms=False)]))
    assert a == b == c
    assert a[0].t == T0 and a[0].h == D(2) and a[0].n == 10


def test_checksum_is_enforced():
    body = zip_csv([row(0, 1, 1, 1, 1)])
    good, bad = hashlib.sha256(body).hexdigest(), "0" * 64

    def handler(checksum):
        def h(request: httpx.Request):
            if request.url.path.endswith(".CHECKSUM"):
                return httpx.Response(200, text=f"{checksum}  file.zip")
            return httpx.Response(200, content=body)
        return h

    async def go(checksum):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler(checksum))) as c:
            return await fetch_verified(c, "x.zip")

    assert asyncio.run(go(good))[1] == good
    with pytest.raises(ChecksumError):
        asyncio.run(go(bad))

    async def missing():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
            return await fetch_verified(c, "x.zip")

    assert asyncio.run(missing()) is None
    assert BASE_URL.startswith("https://data.binance.vision/")


def test_plan_prefers_monthly_and_skips_done():
    today = date(2026, 11, 16)
    plan = plan_files("BTCUSDT", date(2026, 9, 1), today, done={monthly_path("BTCUSDT", "1m", date(2026, 9, 1))})
    paths = [p for p, _, _ in plan]
    assert monthly_path("BTCUSDT", "1m", date(2026, 9, 1)) not in paths  # already imported
    assert not any("2026-09-" in p for p in paths)  # month done -> no daily fallbacks either
    assert monthly_path("BTCUSDT", "1m", date(2026, 10, 1)) in paths
    assert daily_path("BTCUSDT", "1m", date(2026, 10, 31)) in paths  # fallback if the monthly is late
    assert daily_path("BTCUSDT", "1m", date(2026, 11, 15)) in paths
    assert daily_path("BTCUSDT", "1m", date(2026, 11, 16)) not in paths  # today is not published yet
    assert monthly_path("BTCUSDT", "1m", date(2026, 11, 1)) not in paths  # current month incomplete


def test_symbol_rules():
    assert candidate_symbol("BTC")[0] == "BTCUSDT"
    assert candidate_symbol("kPEPE")[0] == "1000PEPEUSDT"
    assert candidate_symbol("xyz:GOLD")[0] is None
    assert candidate_symbol("@142")[0] is None


def test_compare_ratio_and_basis():
    klines = [Kline(T0 + timedelta(minutes=i), D(100), D(100), D(100), D(100), D(1), 1) for i in range(30)]
    ref = [(T0 + timedelta(minutes=i, seconds=20), 100.05) for i in range(30)]
    med, p95, n = compare(ref, klines, D(1))
    assert n == 30 and med == pytest.approx(1.0005) and p95 == pytest.approx(0, abs=1e-6)
    med, _, _ = compare(ref, klines, D(1000))
    assert med == pytest.approx(0.0010005)  # a unit mismatch is obvious


def test_store_and_aggregate_ohlcv(sf):
    # 8 hours of 1m candles: open rises by 1 per minute
    ks = [Kline(T0 + timedelta(minutes=i), D(i), D(i) + D("0.5"), D(i) - D("0.5"), D(i) + D("0.25"), D(2), 3)
          for i in range(8 * 60)]
    with sf() as s:
        assert store_klines(s, "BTC", ks, T0) == 480
        aggregate(s, "BTC", T0, T0 + timedelta(hours=8))
        s.commit()
        bars = s.execute(text("""SELECT t, o, h, l, c, v, n FROM market_candle WHERE source = 'binance_um'
                                 AND coin = 'BTC' AND interval = '4h' ORDER BY t""")).all()
        assert len(bars) == 2
        t, o, h, l, c, v, n = bars[1]  # noqa: E741
        assert t == T0 + timedelta(hours=4)
        assert (o, h, l, c) == (D(240), D("479.5"), D("239.5"), D("479.25"))
        assert v == D(480) and n == 720
        # re-importing the same file is idempotent
        assert store_klines(s, "BTC", ks, T0) == 480
        s.commit()
        assert s.execute(text("SELECT count(*) FROM market_candle WHERE interval = '1m'")).scalar_one() == 480
