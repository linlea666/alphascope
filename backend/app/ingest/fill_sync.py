"""Archive fills for queued wallets before they fall out of the API window.

userFillsByTime returns at most 2000 fills per page and only about the latest 10k fills per wallet,
so for active traders every day of delay loses history for good. Each wallet is synced forward from
its last stored fill; the first sync starts at `requested_start`.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import IngestRun, RawObject
from app.sources.hl_client import HyperliquidInfoClient
from app.sources.raw_store import RawStore

log = logging.getLogger(__name__)

PAGE_SIZE = 2000
RESYNC_AFTER = timedelta(hours=24)
# PnL movement before the earliest fill we could get means trading we can no longer see.
TRUNCATION_PNL_USD = 100.0
TRUNCATION_PNL_SHARE = 0.01


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def fill_rows(wallet_id: int, data: list[dict], raw_id: int | None) -> list[dict]:
    def dec(v):
        return Decimal(v) if v not in (None, "") else None

    return [
        {"w": wallet_id, "tid": int(f["tid"]), "side": f["side"],
         "time": datetime.fromtimestamp(f["time"] / 1000, tz=UTC), "coin": f["coin"], "px": Decimal(f["px"]),
         "sz": Decimal(f["sz"]), "dir": f.get("dir", ""), "sp": dec(f.get("startPosition")),
         "cp": dec(f.get("closedPnl")), "fee": dec(f.get("fee")), "ft": f.get("feeToken"),
         "oid": f.get("oid"), "crossed": f.get("crossed"), "twap": f.get("twapId"), "hash": f.get("hash"),
         "liq": f.get("liquidation"), "raw": raw_id}
        for f in data
    ]


def store_fills(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = text("""
        INSERT INTO fill (wallet_id, tid, side, time, coin, px, sz, dir, start_position, closed_pnl, fee, fee_token,
                          oid, crossed, twap_id, hash, liquidation, raw_object_id)
        VALUES (:w, :tid, :side, :time, :coin, :px, :sz, :dir, :sp, :cp, :fee, :ft, :oid, :crossed, :twap, :hash,
                :liq, :raw)
        ON CONFLICT DO NOTHING""").bindparams(bindparam("liq", type_=JSONB))
    return session.execute(stmt, rows).rowcount


def select_fill_batch(session: Session, limit: int, now: datetime) -> list[tuple]:
    """Never-synced first (by priority), then incomplete, then stale re-syncs."""
    return session.execute(text("""
        SELECT f.wallet_id, w.address, f.requested_start, f.latest_fill_at, f.n_fills
        FROM fill_sync f JOIN wallet w ON w.id = f.wallet_id
        WHERE f.status = 'queued' OR f.last_synced_at IS NULL OR f.last_synced_at < :stale
        ORDER BY (f.last_synced_at IS NOT NULL), (f.status <> 'queued'), f.priority DESC
        LIMIT :limit"""), {"stale": now - RESYNC_AFTER, "limit": limit}).all()


def detect_truncation(session: Session, wallet_id: int, requested_start: datetime, earliest: datetime) -> bool:
    """True when portfolio PnL moved between requested_start and the earliest fill we could fetch."""
    if earliest - requested_start < timedelta(days=1):
        return False
    row = session.execute(text("""
        SELECT max(pnl) - min(pnl), avg(account_value) FROM equity_point
        WHERE wallet_id = :w AND series = 'allTime' AND ts BETWEEN :a AND :b"""),
        {"w": wallet_id, "a": requested_start, "b": earliest}).one()
    moved, avg_av = (float(row[0]) if row[0] is not None else 0.0), float(row[1] or 0)
    return moved > max(TRUNCATION_PNL_USD, TRUNCATION_PNL_SHARE * avg_av)


async def sync_wallet(client: HyperliquidInfoClient, sf: sessionmaker[Session], store: RawStore, run_id: int,
                      wallet_id: int, address: str, requested_start: datetime, latest: datetime | None,
                      max_pages: int) -> dict:
    start = _ms(latest) if latest else _ms(requested_start)
    first_sync = latest is None
    pages = inserted = 0
    earliest_seen: datetime | None = None
    complete = False
    while pages < max_pages:
        payload = {"type": "userFillsByTime", "user": address, "startTime": start}
        r = await client.info(payload)
        pages += 1
        data = r.data or []
        fetched_at = datetime.now(UTC)
        with sf() as s:
            raw_id = None
            if data:
                stored = store.put("userFillsByTime", f"{address}-{start}", fetched_at, r.body)
                raw = RawObject(source="userFillsByTime", request=payload, fetched_at=fetched_at,
                                storage_path=stored.path, sha256=stored.sha256, size_bytes=stored.size_bytes,
                                ingest_run_id=run_id)
                s.add(raw)
                s.flush()
                raw_id = raw.id
            inserted += store_fills(s, fill_rows(wallet_id, data, raw_id))
            s.commit()
        if data:
            t_first = datetime.fromtimestamp(data[0]["time"] / 1000, tz=UTC)
            earliest_seen = t_first if earliest_seen is None else min(earliest_seen, t_first)
        if len(data) < PAGE_SIZE:
            complete = True
            break
        last_ms = data[-1]["time"]
        if last_ms <= start:  # a full page inside one millisecond: cannot advance safely
            break
        start = last_ms  # same-ms fills are deduplicated by the primary key

    with sf() as s:
        cov = s.execute(text("SELECT min(time), max(time), count(*) FROM fill WHERE wallet_id = :w"),
                        {"w": wallet_id}).one()
        truncated = None
        if first_sync and cov[0] is not None:
            truncated = detect_truncation(s, wallet_id, requested_start, cov[0])
        s.execute(text("""
            UPDATE fill_sync SET status = :st, earliest_fill_at = :e, latest_fill_at = :l, n_fills = :n,
              truncated = coalesce(:tr, truncated), last_synced_at = :now, error = NULL
            WHERE wallet_id = :w"""),
            {"st": "synced" if complete else "queued", "e": cov[0], "l": cov[1], "n": cov[2], "tr": truncated,
             "now": datetime.now(UTC), "w": wallet_id})
        s.commit()
    return {"pages": pages, "inserted": inserted, "complete": complete}


async def sync_fills(sf: sessionmaker[Session], settings: Settings, store: RawStore, limit: int | None = None) -> dict:
    t0 = time.monotonic()
    now = datetime.now(UTC)
    with sf() as s:
        run = IngestRun(kind="fills", status="running", stats={})
        s.add(run)
        s.commit()
        run_id = run.id
        batch = select_fill_batch(s, limit or settings.fill_batch_size, now)
    ok = failed = pages = inserted = 0
    errors: list[str] = []
    budget = settings.hl_weight_per_minute * settings.hl_weight_safety
    async with HyperliquidInfoClient(settings.hl_info_url, budget, settings.hl_timeout_seconds,
                                     settings.hl_max_retries) as client:
        for wallet_id, address, requested_start, latest, _ in batch:
            try:
                r = await sync_wallet(client, sf, store, run_id, wallet_id, address, requested_start, latest,
                                      settings.fill_max_pages)
                ok += 1
                pages += r["pages"]
                inserted += r["inserted"]
            except Exception as exc:  # noqa: BLE001 — one wallet must not stop the batch
                failed += 1
                errors.append(f"{address}: {exc!r}"[:300])
                with sf() as s:
                    s.execute(text("UPDATE fill_sync SET error = :e, last_synced_at = :now WHERE wallet_id = :w"),
                              {"e": repr(exc)[:500], "now": datetime.now(UTC), "w": wallet_id})
                    s.commit()
        stats = {"selected": len(batch), "ok": ok, "failed": failed, "pages": pages, "inserted": inserted,
                 "errors": errors[:20], "api": client.usage.as_dict(), "duration_s": round(time.monotonic() - t0, 2)}
    with sf() as s:
        run = s.get(IngestRun, run_id)
        run.status = "success" if not failed else ("partial" if ok else "failed")
        run.finished_at, run.stats = datetime.now(UTC), stats
        s.commit()
    return {"run_id": run_id, "status": run.status, **stats}


if __name__ == "__main__":  # pragma: no cover
    from app.config import get_settings
    from app.db import session_factory
    from app.sources.raw_store import LocalRawStore

    logging.basicConfig(level=logging.INFO)
    st = get_settings()
    print(asyncio.run(sync_fills(session_factory(), st, LocalRawStore(st.raw_store_dir), 3)))
