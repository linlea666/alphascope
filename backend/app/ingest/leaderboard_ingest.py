"""Leaderboard ingest: archive raw -> parse -> upsert wallets -> detect new/disappeared -> snapshot -> tiers."""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import LEADERBOARD_WINDOWS, METRIC_FIELDS, IngestRun, LeaderboardSnapshot, RawObject
from app.quant.quality import leaderboard_flags
from app.sources.leaderboard import fetch_leaderboard, parse_leaderboard
from app.sources.raw_store import RawStore
from app.tiering import recompute_tiers

log = logging.getLogger(__name__)

VALUE_COLS = ["account_value"] + [f"{w}_{f}" for w in LEADERBOARD_WINDOWS for f in METRIC_FIELDS]
TMP_COLS = ["address", "display_name", *VALUE_COLS, "quality_flags"]


@dataclass
class LeaderboardInput:
    """Pre-fetched body (CLI --file / tests). Real network fetch is used when absent."""

    body: bytes
    etag: str | None = None
    last_modified: str | None = None


def _finish(session: Session, run: IngestRun, status: str, stats: dict, error: str | None = None) -> dict:
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.stats = stats
    run.error = error
    session.commit()
    return {"run_id": run.id, "status": status, **stats}


def ingest_leaderboard(sf: sessionmaker[Session], settings: Settings, store: RawStore,
                       given: LeaderboardInput | None = None, now: datetime | None = None) -> dict:
    t0 = time.monotonic()
    with sf() as session:
        run = IngestRun(kind="leaderboard", status="running", stats={})
        session.add(run)
        session.commit()
        try:
            return _ingest(session, run, settings, store, given, now, t0)
        except Exception as exc:
            session.rollback()
            log.exception("leaderboard ingest failed")
            return _finish(session, run, "failed", {"duration_s": round(time.monotonic() - t0, 2)}, repr(exc))


def _ingest(session: Session, run: IngestRun, settings: Settings, store: RawStore,
            given: LeaderboardInput | None, now: datetime | None, t0: float) -> dict:
    last = session.scalars(select(RawObject).where(RawObject.source == "leaderboard")
                           .order_by(RawObject.fetched_at.desc()).limit(1)).first()
    if given is None:
        fetched = fetch_leaderboard(settings.hl_leaderboard_url, etag=last.etag if last else None)
        if fetched.not_modified:
            return _finish(session, run, "not_modified", {"reason": "http_304"})
        body, etag, last_modified = fetched.body, fetched.etag, fetched.last_modified
    else:
        body, etag, last_modified = given.body, given.etag, given.last_modified
    now = now or datetime.now(UTC)

    sha = hashlib.sha256(body).hexdigest()
    if last is not None and last.sha256 == sha:
        return _finish(session, run, "not_modified", {"reason": "same_content", "sha256": sha})
    stored = store.put("leaderboard", now.strftime("%Y%m%dT%H%M%SZ"), now, body)
    raw =RawObject(source="leaderboard", request={"url": settings.hl_leaderboard_url}, fetched_at=now,
                    storage_path=stored.path, sha256=stored.sha256, size_bytes=stored.size_bytes,
                    etag=etag, last_modified=last_modified, ingest_run_id=run.id)
    session.add(raw)
    session.flush()

    parsed = parse_leaderboard(body)
    t_parse = time.monotonic() - t0
    rows = []
    flag_counts: dict[str, int] = {}
    for e in parsed.entries:
        flags = leaderboard_flags(e)
        for f in flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
        values = [e.account_value] + [v for w in LEADERBOARD_WINDOWS for v in e.windows[w]]
        rows.append((e.address, e.display_name, *values, flags))

    existing = session.execute(text("SELECT count(*) FROM wallet WHERE venue = 'hyperliquid'")).scalar_one()
    session.execute(text(f"""
        CREATE TEMP TABLE tmp_lb (address varchar(42) PRIMARY KEY, display_name text,
          {", ".join(f"{c} numeric" for c in VALUE_COLS)}, quality_flags text[]) ON COMMIT DROP"""))
    pg = session.connection().connection.driver_connection
    with pg.cursor() as cur, cur.copy(f"COPY tmp_lb ({', '.join(TMP_COLS)}) FROM STDIN") as copy:
        for r in rows:
            copy.write_row(r)

    assign = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*VALUE_COLS, "quality_flags", "display_name"])
    inserted = session.execute(text(f"""
        INSERT INTO wallet (address, display_name, first_seen_at, first_seen_source, last_seen_at,
                            present_in_latest, {", ".join(VALUE_COLS)}, quality_flags, leaderboard_fetched_at)
        SELECT address, display_name, :now, 'leaderboard', :now, true, {", ".join(VALUE_COLS)},
               quality_flags, :now
        FROM tmp_lb
        ON CONFLICT (address) DO UPDATE SET {assign}, last_seen_at = EXCLUDED.last_seen_at,
          present_in_latest = true, removed_at = NULL, leaderboard_fetched_at = EXCLUDED.leaderboard_fetched_at
        RETURNING (xmax = 0) AS inserted"""), {"now": now}).scalars().all()
    new_wallets = sum(1 for i in inserted if i)
    disappeared = session.execute(text("""
        UPDATE wallet SET present_in_latest = false, removed_at = :now
        WHERE present_in_latest AND last_seen_at < :now"""), {"now": now}).rowcount

    last_persisted = session.scalars(select(LeaderboardSnapshot.fetched_at)
                                     .where(LeaderboardSnapshot.rows_persisted)
                                     .order_by(LeaderboardSnapshot.fetched_at.desc()).limit(1)).first()
    persist = last_persisted is None or now - last_persisted >= timedelta(hours=settings.leaderboard_snapshot_hours)
    snap = LeaderboardSnapshot(ingest_run_id=run.id, raw_object_id=raw.id, fetched_at=now,
                               source_last_modified=last_modified, row_count=len(rows), rows_persisted=persist)
    session.add(snap)
    session.flush()
    if persist:
        cols = ", ".join([*VALUE_COLS, "display_name", "quality_flags"])
        session.execute(text(f"""
            INSERT INTO leaderboard_row (snapshot_id, wallet_id, {cols})
            SELECT :sid, w.id, {", ".join(f"t.{c}" for c in [*VALUE_COLS, "display_name", "quality_flags"])}
            FROM tmp_lb t JOIN wallet w ON w.address = t.address"""), {"sid": snap.id})

    tiers = recompute_tiers(session, now)
    stats = {
        "rows": len(rows),
        "initial_import": existing == 0,
        "new_wallets": new_wallets,
        "disappeared_wallets": disappeared,
        "duplicates_in_file": parsed.duplicates,
        "unknown_windows": sorted(parsed.unknown_windows),
        "extra_fields": sorted(parsed.extra_fields),
        "snapshot_id": snap.id,
        "rows_persisted": persist,
        "flags": flag_counts,
        "tiers": tiers,
        "size_bytes": stored.size_bytes,
        "fetch_parse_s": round(t_parse, 2),
        "duration_s": round(time.monotonic() - t0, 2),
    }
    return _finish(session, run, "success", stats)
