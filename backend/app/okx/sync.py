"""Per-trader OKX collection: position history (backfill + increments), profiles, assets and contract specs.

History pages come newest first; `after` is the uTime (ms) of the oldest record already seen. Backfill resumes
from the stored cursor until the target window is covered or OKX has nothing older; an empty or short page is
re-requested once before it is trusted as the end. Increments read from the newest down to what we already have.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.okx.client import OkxClient, OkxError
from app.okx.positions import derive_wallet, parse_inst, upsert_positions

log = logging.getLogger(__name__)

PAGE = 100
# How often a trader with complete history is checked for new positions, by pool status
INCREMENT_EVERY = {"active": timedelta(hours=1), "watching": timedelta(hours=1), "inactive": timedelta(days=1),
                   "dropped": timedelta(days=7), "error_backoff": timedelta(days=1)}
STABLE = {"USDT", "USDC", "USD"}


def _ms(t: datetime | None) -> int | None:
    return int(t.timestamp() * 1000) if t else None


def due_traders(s: Session, now: datetime, limit: int) -> list:
    """By priority (0 watched or followed, 1 new on the board, 2 on a board, 3 others), backfills before increments."""
    cases = " ".join(f"WHEN '{k}' THEN interval '{int(v.total_seconds())} seconds'" for k, v in INCREMENT_EVERY.items())
    return s.execute(text(f"""
        SELECT t.wallet_id, w.handle, t.history_status, t.cursor_after, t.newest_at, t.oldest_at, t.target_days,
               t.failure_count
        FROM okx_trader t JOIN wallet w ON w.id = t.wallet_id
        WHERE w.handle IS NOT NULL AND (t.retry_after IS NULL OR t.retry_after <= :now)
          AND (t.history_status IN ('partial', 'error_backoff')
               OR t.history_synced_at IS NULL
               OR t.history_synced_at < :now - CASE t.pool_status {cases} ELSE interval '1 day' END)
        ORDER BY t.priority, (t.history_status IN ('partial', 'error_backoff')) DESC, t.history_synced_at NULLS FIRST
        LIMIT :n"""), {"now": now, "n": limit}).all()


async def _page(client: OkxClient, handle: str, after: str | None) -> list[dict]:
    data = await client.history(handle, after, PAGE)
    if len(data) < PAGE:  # empty or short pages are occasionally transient: confirm before ending
        again = await client.history(handle, after, PAGE)
        if len(again) > len(data):
            return again
    return data


async def sync_trader(sf: sessionmaker[Session], client: OkxClient, row, now: datetime, max_pages: int) -> dict:
    wid, handle = row.wallet_id, row.handle
    stored = 0
    status = row.history_status if row.history_status in ("target_complete", "source_complete") else "partial"
    cursor = row.cursor_after
    cutoff = _ms(now - timedelta(days=row.target_days or 365))
    try:
        # 1) increments: newest positions down to what we already have
        if row.newest_at is not None:
            after, newest_known = None, _ms(row.newest_at)
            for _ in range(max_pages):
                data = await _page(client, handle, after)
                with sf() as s:
                    stored += upsert_positions(s, wid, data, now)
                    s.commit()
                if len(data) < PAGE or min(int(r["uTime"]) for r in data) <= newest_known:
                    break
                after = str(min(int(r["uTime"]) for r in data))
        # 2) backfill: older positions from the cursor until the target window or the source is exhausted
        if status == "partial":
            after = cursor or (str(_ms(row.oldest_at)) if row.oldest_at else None)
            for _ in range(max_pages):
                data = await _page(client, handle, after)
                with sf() as s:
                    stored += upsert_positions(s, wid, data, now)
                    s.commit()
                if not data:
                    status = "source_complete"
                    break
                oldest = min(int(r["uTime"]) for r in data)
                after = cursor = str(oldest)
                if cutoff and oldest <= cutoff:
                    status = "target_complete"
                    break
                if len(data) < PAGE:
                    status = "source_complete"
                    break
    except OkxError as exc:
        fc = (row.failure_count or 0) + 1
        with sf() as s:
            s.execute(text("""
                UPDATE okx_trader SET failure_count = :fc, retry_after = :ra, last_error = :e, cursor_after = :cur,
                  history_status = CASE WHEN history_status = 'partial' THEN 'error_backoff' ELSE history_status END,
                  updated_at = :now WHERE wallet_id = :w"""),
                {"fc": fc, "ra": now + timedelta(seconds=min(3600, 60 * 2 ** min(fc, 5))), "e": str(exc)[:500],
                 "cur": cursor, "now": now, "w": wid})
            if stored:
                _refresh_counts(s, wid)
                derive_wallet(s, wid)
            s.commit()
        return {"stored": stored, "error": str(exc)}
    with sf() as s:
        s.execute(text("""
            UPDATE okx_trader SET history_status = :st, cursor_after = :cur, history_synced_at = :now,
              failure_count = 0, retry_after = NULL, last_error = NULL, updated_at = :now,
              pool_status = CASE WHEN pool_status = 'error_backoff' THEN 'inactive' ELSE pool_status END
            WHERE wallet_id = :w"""), {"st": status, "cur": cursor, "now": now, "w": wid})
        _refresh_counts(s, wid)
        if stored:
            derive_wallet(s, wid)
        s.commit()
    return {"stored": stored, "status": status}


def _refresh_counts(s: Session, wid: int) -> None:
    s.execute(text("""
        UPDATE okx_trader t SET n_positions = p.n, oldest_at = p.oldest, newest_at = p.newest
        FROM (SELECT count(*) AS n, min(close_time) AS oldest, max(close_time) AS newest
              FROM okx_position WHERE wallet_id = :w) p
        WHERE t.wallet_id = :w"""), {"w": wid})


async def sync_history(sf: sessionmaker[Session], settings: Settings, client: OkxClient, limit: int | None = None,
                       now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        rows = due_traders(s, now, limit or settings.okx_history_batch)
    out = {"traders": 0, "stored": 0, "errors": 0, "completed": 0}
    for row in rows:
        r = await sync_trader(sf, client, row, now, settings.okx_history_max_pages)
        out["traders"] += 1
        out["stored"] += r["stored"]
        out["errors"] += "error" in r
        out["completed"] += r.get("status") in ("target_complete", "source_complete") and row.history_status not in (
            "target_complete", "source_complete")
    return out


async def resolve_profiles(sf: sessionmaker[Session], client: OkxClient, limit: int = 50) -> dict:
    """Fill in uniqueName (needed for history requests), nickname and avatar for traders that lack them."""
    with sf() as s:
        rows = s.execute(text("""
            SELECT w.id, w.venue_uid FROM wallet w JOIN okx_trader t ON t.wallet_id = w.id
            WHERE w.venue = 'okx' AND w.handle IS NULL AND (t.retry_after IS NULL OR t.retry_after <= now())
            ORDER BY t.priority, t.last_seen_official_at DESC NULLS LAST LIMIT :n"""), {"n": limit}).all()
    ok = failed = 0
    for wid, uid in rows:
        try:
            p = await client.profile(uid)
            handle = str(p.get("uniqueName") or "").strip() or None
            if not handle:
                raise OkxError("user-info: no uniqueName")
        except OkxError as exc:
            failed += 1
            with sf() as s:
                s.execute(text("""UPDATE okx_trader SET last_error = :e, retry_after = now() + interval '1 day'
                                  WHERE wallet_id = :w"""), {"e": str(exc)[:500], "w": wid})
                s.commit()
            continue
        keep = {k: p.get(k) for k in ("bio", "countryName", "joinTime", "followerCount", "nickName", "uniqueName",
                                      "certification", "publicStatus") if k in p}
        with sf() as s:
            s.execute(text("""
                UPDATE wallet SET handle = :h, display_name = coalesce(:n, display_name),
                  avatar_url = coalesce(:av, avatar_url) WHERE id = :w"""),
                {"h": handle, "n": p.get("nickName") or None, "av": p.get("portrait") or None, "w": wid})
            s.execute(text("""UPDATE okx_trader SET profile = CAST(:p AS jsonb), profile_synced_at = now(),
                              last_error = NULL WHERE wallet_id = :w"""),
                      {"p": json.dumps(keep, default=str), "w": wid})
            s.commit()
        ok += 1
    return {"resolved": ok, "failed": failed}


async def refresh_assets(sf: sessionmaker[Session], client: OkxClient, limit: int = 100) -> dict:
    """Lead-trading assets in USD for active / watched traders whose figure is older than a day.

    Traders on today's board already got their asset from the board itself.
    """
    with sf() as s:
        rows = s.execute(text("""
            SELECT t.wallet_id, w.handle FROM okx_trader t JOIN wallet w ON w.id = t.wallet_id
            WHERE w.handle IS NOT NULL AND t.pool_status IN ('active', 'watching')
              AND (t.asset_at IS NULL OR t.asset_at < now() - interval '1 day')
            ORDER BY t.priority, t.asset_at NULLS FIRST LIMIT :n"""), {"n": limit}).all()
    prices: dict[str, float | None] = {}
    ok = failed = 0
    for wid, handle in rows:
        try:
            items = await client.asset(handle)
            total = 0.0
            for it in items:
                ccy, amount = str(it.get("currency") or "").upper(), float(it.get("amount") or 0)
                if ccy in STABLE:
                    total += amount
                    continue
                if ccy not in prices:
                    prices[ccy] = await client.index_price(ccy)
                if prices[ccy] is None:
                    raise OkxError(f"asset: no USD price for {ccy}")
                total += amount * prices[ccy]
        except (OkxError, ValueError, TypeError) as exc:
            failed += 1
            log.info("okx asset %s: %s", handle, exc)
            continue
        with sf() as s:
            # an empty list is an absence of evidence, not a zero balance
            s.execute(text("""UPDATE okx_trader SET asset = CASE WHEN :has THEN :a ELSE asset END,
                              asset_at = now() WHERE wallet_id = :w"""), {"has": bool(items), "a": total, "w": wid})
            s.commit()
        ok += 1
    return {"assets": ok, "failed": failed}


async def refresh_instruments(sf: sessionmaker[Session], client: OkxClient) -> int:
    rows = []
    for it in await client.instruments():
        try:
            coin, ct_type = parse_inst(it["instId"])
            rows.append({"i": it["instId"], "c": coin, "t": it.get("ctType") or ct_type, "v": it["ctVal"],
                         "vc": it.get("ctValCcy") or None, "sc": it.get("settleCcy") or None,
                         "ml": int(float(it["lever"])) if it.get("lever") else None, "st": it.get("state") or None})
        except (KeyError, ValueError):
            continue
    with sf() as s:
        if rows:
            s.execute(text("""
                INSERT INTO okx_instrument (inst_id, coin, ct_type, ct_val, ct_val_ccy, settle_ccy, max_lever, state,
                                            updated_at)
                VALUES (:i, :c, :t, :v, :vc, :sc, :ml, :st, now())
                ON CONFLICT (inst_id) DO UPDATE SET coin = EXCLUDED.coin, ct_type = EXCLUDED.ct_type,
                  ct_val = EXCLUDED.ct_val, ct_val_ccy = EXCLUDED.ct_val_ccy, settle_ccy = EXCLUDED.settle_ccy,
                  max_lever = EXCLUDED.max_lever, state = EXCLUDED.state, updated_at = now()"""), rows)
        s.commit()
    return len(rows)
