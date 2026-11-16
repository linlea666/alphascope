import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from app.ingest.fill_sync import PAGE_SIZE, detect_truncation, sync_wallet
from app.sources.hl_client import HyperliquidInfoClient, WeightBucket
from app.sources.raw_store import LocalRawStore

START = datetime(2026, 7, 1, tzinfo=UTC)
START_MS = int(START.timestamp() * 1000)


def fake_fill(i: int, t_ms: int, coin="BTC") -> dict:
    return {"coin": coin, "px": "60000.0", "sz": "0.1", "side": "B" if i % 2 else "A", "time": t_ms,
            "startPosition": "0.0", "dir": "Open Long", "closedPnl": "0.0", "hash": "0x" + "0" * 64,
            "oid": i, "crossed": True, "fee": "0.1", "tid": 10_000 + i, "feeToken": "USDC", "twapId": None}


def make_client(pages: list[list[dict]], calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        page = pages[len(calls) - 1] if len(calls) <= len(pages) else []
        return httpx.Response(200, json=page)

    return HyperliquidInfoClient("https://example.invalid/info", 10_000, transport=httpx.MockTransport(handler),
                                 bucket=WeightBucket(1_000_000))


def seed_wallet(sf) -> int:
    with sf() as s:
        wid = s.execute(text("""
            INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at, present_in_latest)
            VALUES (:a, now(), 'test', now(), true) RETURNING id"""), {"a": "0x" + "c" * 40}).scalar_one()
        s.execute(text("""INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at)
                          VALUES (:w, 'test', 1, :st, 'queued', 0, now())"""), {"w": wid, "st": START})
        s.commit()
    return wid


def test_paginates_forward_and_dedupes_boundary_fills(sf, tmp_path):
    wid = seed_wallet(sf)
    page1 = [fake_fill(i, START_MS + i * 1000) for i in range(PAGE_SIZE)]
    # The next page starts at the last timestamp again, so its first fill repeats page 1's last one.
    page2 = [page1[-1]] + [fake_fill(PAGE_SIZE + i, START_MS + (PAGE_SIZE + i) * 1000) for i in range(10)]
    calls: list[dict] = []

    async def go():
        async with make_client([page1, page2], calls) as c:
            return await sync_wallet(c, sf, LocalRawStore(tmp_path), None, wid, "0x" + "c" * 40, START, None, 5)

    r = asyncio.run(go())
    assert r["pages"] == 2 and r["complete"]
    assert calls[0]["startTime"] == START_MS and calls[1]["startTime"] == page1[-1]["time"]
    with sf() as s:
        assert s.execute(text("SELECT count(*) FROM fill")).scalar_one() == PAGE_SIZE + 10
        st = s.execute(text("SELECT status, n_fills, latest_fill_at FROM fill_sync")).one()
        assert st.status == "synced" and st.n_fills == PAGE_SIZE + 10


def test_stops_at_max_pages_and_stays_queued(sf, tmp_path):
    wid = seed_wallet(sf)
    pages = [[fake_fill(p * PAGE_SIZE + i, START_MS + (p * PAGE_SIZE + i) * 1000) for i in range(PAGE_SIZE)]
             for p in range(3)]
    calls: list[dict] = []

    async def go():
        async with make_client(pages, calls) as c:
            return await sync_wallet(c, sf, LocalRawStore(tmp_path), None, wid, "0x" + "c" * 40, START, None, 2)

    r = asyncio.run(go())
    assert r["pages"] == 2 and not r["complete"]
    with sf() as s:
        assert s.execute(text("SELECT status FROM fill_sync")).scalar_one() == "queued"


def test_truncation_detected_from_pnl_before_first_fill(sf):
    wid = seed_wallet(sf)
    with sf() as s:
        for d, pnl in ((0, 0), (10, 5000), (20, 5200)):
            s.execute(text("""INSERT INTO equity_point (wallet_id, series, ts, account_value, pnl, fetched_at)
                              VALUES (:w, 'allTime', :ts, 100000, :p, now())"""),
                      {"w": wid, "ts": START + timedelta(days=d), "p": pnl})
        s.commit()
        assert detect_truncation(s, wid, START, START + timedelta(days=20)) is True
        assert detect_truncation(s, wid, START, START + timedelta(hours=12)) is False
