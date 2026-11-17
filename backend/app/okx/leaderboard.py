"""OKX lead-trader leaderboards: a verified daily crawl of the full 7-day board, plus the top of the 30/90/365-day
boards. New faces become tracked traders whose position history is backfilled next.

A full crawl is only accepted when every page carries the same dataVersion and total as page one, pages have the
expected size, nobody appears twice, page one is unchanged at the end and the unique count equals the total; a
board republished mid-crawl restarts it (up to three times). Partial (top-page) crawls only add presence, never
mark anyone absent.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.config import Settings
from app.okx.client import OkxClient, OkxError
from app.traders import okx_key

log = logging.getLogger(__name__)

EXTRA_WINDOWS = ("30d", "90d", "365d")
DROP_AFTER = timedelta(days=7)  # not seen on any board for this long -> pool 'dropped'


class VersionChanged(OkxError):
    pass


@dataclass
class Member:
    author_id: str
    nick: str | None
    portrait: str | None
    rank: int
    asset: float | None
    pnl: float | None
    pnl_ratio: float | None
    win_rate: float | None
    max_drawdown: float | None
    rates: list = field(default_factory=list)


@dataclass
class Crawl:
    window: str
    version: str
    total: int
    pages: int
    pages_done: int
    full: bool
    members: list[Member]


def _num(v) -> float | None:
    if v is None or (isinstance(v, str) and v.strip() in ("", "-", "--")):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _meta(data: dict, size: int) -> tuple[str, int, int]:
    def count(key: str) -> int:
        v = data.get(key)
        if isinstance(v, bool) or not str(v).isdigit():
            raise OkxError(f"leaderboard: {key} is not a non-negative integer")
        return int(v)

    total, pages = count("total"), count("pages")
    version = str(data.get("dataVersion") or "")
    if not version or (total > 0 and pages != math.ceil(total / size)) or (total == 0 and pages not in (0, 1)):
        raise OkxError("leaderboard: version, total and page count disagree")
    return version, total, pages


def _member(item: dict, rank: int) -> Member:
    aid = str(item.get("authorId") or "").strip()
    if not aid.isdigit():
        raise OkxError("leaderboard: member without authorId")
    asset = _num(item.get("asset"))
    return Member(aid, item.get("nickName") or None, item.get("portrait") or None, rank,
                  asset if asset is not None and asset >= 0 else None, _num(item.get("pnl")),
                  _num(item.get("pnlRatio")), _num(item.get("winRate")), _num(item.get("maxRetreat")),
                  [[r.get("statTime"), r.get("value")] for r in item.get("rates") or [] if isinstance(r, dict)])


async def crawl(client: OkxClient, window: str, size: int, max_pages: int | None = None) -> Crawl:
    for restart in range(3):
        try:
            first = await client.leaderboard(window, 1, size)
            expected = _meta(first, size)
            version, total, pages = expected
            n_pages = pages if max_pages is None else min(pages, max_pages)
            full = n_pages == pages
            members: list[Member] = []
            seen: set[str] = set()
            for page in range(1, max(1, n_pages) + 1):
                data = first if page == 1 else await client.leaderboard(window, page, size)
                if _meta(data, size) != expected:
                    raise VersionChanged("leaderboard republished during the crawl")
                ranks = data["ranks"]
                want = min(size, max(0, total - (page - 1) * size))
                if len(ranks) != want:
                    raise OkxError(f"leaderboard page {page}: {len(ranks)} rows, expected {want}")
                for offset, item in enumerate(ranks):
                    m = _member(item, (page - 1) * size + offset + 1)
                    if m.author_id in seen:
                        raise OkxError(f"leaderboard page {page}: duplicate trader {m.author_id}")
                    seen.add(m.author_id)
                    members.append(m)
            if full:
                confirm = await client.leaderboard(window, 1, size)
                if _meta(confirm, size) != expected or \
                        [str(x.get("authorId")) for x in confirm["ranks"]] != [str(x.get("authorId")) for x in first["ranks"]]:
                    raise VersionChanged("leaderboard page one changed before the crawl finished")
                if len(seen) != total:
                    raise OkxError(f"leaderboard: {len(seen)} unique traders, official total {total}")
            return Crawl(window, version, total, pages, n_pages, full, members)
        except VersionChanged:
            if restart == 2:
                raise
            log.info("okx %s board republished mid-crawl; restarting", window)
    raise RuntimeError("unreachable")


def business_date(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def persist(s: Session, c: Crawl, run_id: int, now: datetime, tz: str) -> dict:
    """Store a crawl: traders, rows, presence, latest assets and pool status. Returns counts."""
    d = business_date(now, tz)
    wallets = [{"a": okx_key(m.author_id), "uid": m.author_id, "n": m.nick, "av": m.portrait, "now": now}
               for m in c.members]
    uids = [m.author_id for m in c.members]
    known = set(s.execute(text("SELECT venue_uid FROM wallet WHERE venue = 'okx' AND venue_uid = ANY(:u)"),
                          {"u": uids}).scalars())
    if wallets:
        s.execute(text("""
            INSERT INTO wallet (address, venue, venue_uid, display_name, avatar_url, first_seen_at, first_seen_source,
                                last_seen_at, present_in_latest)
            VALUES (:a, 'okx', :uid, :n, :av, :now, 'okx_leaderboard', :now, false)
            ON CONFLICT (address) DO UPDATE SET display_name = coalesce(EXCLUDED.display_name, wallet.display_name),
              avatar_url = coalesce(EXCLUDED.avatar_url, wallet.avatar_url), last_seen_at = EXCLUDED.last_seen_at"""),
            wallets)
    ids = dict(s.execute(text("SELECT venue_uid, id FROM wallet WHERE venue = 'okx' AND venue_uid = ANY(:u)"),
                         {"u": uids}).all())
    new = len(set(uids) - known)
    by_id = {m.author_id: m for m in c.members}
    s.execute(text("""
        INSERT INTO okx_trader (wallet_id, history_status, pool_status, priority, target_days, n_positions,
          failure_count, first_pool_source, last_seen_official_at, asset, asset_at, updated_at)
        VALUES (:w, 'partial', 'active', 1, 365, 0, 0, :src, :now, :asset, :asset_at, :now)
        ON CONFLICT (wallet_id) DO UPDATE SET last_seen_official_at = EXCLUDED.last_seen_official_at,
          pool_status = CASE WHEN okx_trader.pool_status IN ('dropped', 'inactive') THEN 'active'
                             ELSE okx_trader.pool_status END,
          priority = least(okx_trader.priority, 2),
          asset = coalesce(EXCLUDED.asset, okx_trader.asset),
          asset_at = CASE WHEN EXCLUDED.asset IS NULL THEN okx_trader.asset_at ELSE EXCLUDED.asset_at END,
          updated_at = EXCLUDED.updated_at"""),
        [{"w": ids[aid], "src": f"official:{c.window}", "now": now, "asset": m.asset,
          "asset_at": now if m.asset is not None else None} for aid, m in by_id.items()])
    s.execute(text("""
        INSERT INTO okx_leaderboard_row (run_id, wallet_id, rank, asset, pnl, pnl_ratio, win_rate, max_drawdown, rates)
        VALUES (:r, :w, :rank, :asset, :pnl, :ratio, :win, :dd, CAST(:rates AS jsonb))"""),
        [{"r": run_id, "w": ids[m.author_id], "rank": m.rank, "asset": m.asset, "pnl": m.pnl, "ratio": m.pnl_ratio,
          "win": m.win_rate, "dd": m.max_drawdown, "rates": json.dumps(m.rates)} for m in c.members])
    s.execute(text("""
        INSERT INTO okx_presence (wallet_id, time_window, first_seen_at, last_seen_at, seen_days, streak, currently_seen,
                                  last_rank, best_rank)
        VALUES (:w, :win, :now, :now, 1, 1, true, :rank, :rank)
        ON CONFLICT (wallet_id, time_window) DO UPDATE SET
          seen_days = okx_presence.seen_days
            + CASE WHEN (okx_presence.last_seen_at AT TIME ZONE :tz)::date < CAST(:d AS date) THEN 1 ELSE 0 END,
          streak = CASE WHEN (okx_presence.last_seen_at AT TIME ZONE :tz)::date = CAST(:d AS date)
                          THEN greatest(okx_presence.streak, 1)
                        WHEN (okx_presence.last_seen_at AT TIME ZONE :tz)::date = CAST(:d AS date) - 1
                          THEN okx_presence.streak + 1
                        ELSE 1 END,
          last_seen_at = EXCLUDED.last_seen_at, currently_seen = true, last_rank = EXCLUDED.last_rank,
          best_rank = least(okx_presence.best_rank, EXCLUDED.best_rank)"""),
        [{"w": ids[m.author_id], "win": c.window, "now": now, "rank": m.rank, "tz": tz, "d": d} for m in c.members])
    absent = 0
    if c.full:
        absent = s.execute(text("""
            UPDATE okx_presence SET currently_seen = false, streak = 0
            WHERE time_window = :win AND currently_seen AND NOT (wallet_id = ANY(:ids))"""),
            {"win": c.window, "ids": list(ids.values())}).rowcount
        s.execute(text("""
            UPDATE okx_trader SET pool_status = 'dropped', updated_at = :now
            WHERE pool_status = 'active' AND (last_seen_official_at IS NULL OR last_seen_official_at < :cut)"""),
            {"now": now, "cut": now - DROP_AFTER})
    s.execute(text("""
        UPDATE okx_leaderboard_run SET status = 'complete', data_version = :v, expected_pages = :ep,
          expected_total = :et, pages_done = :pd, unique_count = :uc, new_traders = :new, completed_at = :now
        WHERE id = :id"""), {"v": c.version, "ep": c.pages, "et": c.total, "pd": c.pages_done,
                             "uc": len(c.members), "new": new, "now": now, "id": run_id})
    return {"window": c.window, "full": c.full, "total": c.total, "members": len(c.members), "new": new,
            "absent": absent}


async def run_window(sf: sessionmaker[Session], client: OkxClient, window: str, size: int, tz: str,
                     max_pages: int | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with sf() as s:
        run_id = s.execute(text("""
            INSERT INTO okx_leaderboard_run (business_date, time_window, status, started_at, pages_done, unique_count,
                                             new_traders, source)
            VALUES (:d, :win, 'running', :now, 0, 0, 0, :src) RETURNING id"""),
            {"d": business_date(now, tz), "win": window, "now": now,
             "src": "crawl" if max_pages is None else "crawl_top"}).scalar_one()
        s.commit()
    try:
        c = await crawl(client, window, size, max_pages)
    except Exception as exc:
        with sf() as s:
            s.execute(text("UPDATE okx_leaderboard_run SET status = 'failed', error = :e, completed_at = :now "
                           "WHERE id = :id"), {"e": str(exc)[:500], "now": datetime.now(UTC), "id": run_id})
            s.commit()
        raise
    with sf() as s:
        r = persist(s, c, run_id, now, tz)
        s.commit()
    return r


async def ensure_daily(sf: sessionmaker[Session], settings: Settings, client: OkxClient,
                       now: datetime | None = None) -> dict:
    """Crawl today's boards once the local crawl time has passed and today's complete 7d run is missing.

    Called every half hour; a failed attempt is retried at the next call (at most every 20 minutes).
    """
    now = now or datetime.now(UTC)
    with sf() as s:
        tz = settings_store.get(s, "live").get("timezone") or "Asia/Shanghai"
        local = now.astimezone(ZoneInfo(tz))
        d = local.date()
        if local.hour + local.minute / 60 < settings.okx_daily_hour_local:
            return {"status": "before_crawl_time"}
        done = s.execute(text("""
            SELECT 1 FROM okx_leaderboard_run WHERE time_window = '7d' AND business_date = :d AND status = 'complete'
              AND source <> 'sqlite_import'"""), {"d": d}).first()
        if done:
            return {"status": "done_today"}
        recent_fail = s.execute(text("""
            SELECT 1 FROM okx_leaderboard_run WHERE time_window = '7d' AND status = 'failed' AND started_at > :t"""),
            {"t": now - timedelta(minutes=20)}).first()
        if recent_fail:
            return {"status": "backoff"}
    out = {"status": "ok", "7d": await run_window(sf, client, "7d", settings.okx_page_size, tz, now=now)}
    for w in EXTRA_WINDOWS:
        try:
            out[w] = await run_window(sf, client, w, settings.okx_page_size, tz, settings.okx_extra_window_pages,
                                      now=now)
        except OkxError as exc:  # the top of the longer boards is a bonus; the 7d board is what matters
            out[w] = {"error": str(exc)}
    return out
