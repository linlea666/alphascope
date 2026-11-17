"""One-time migration of the old local OKX collector (SQLite) into AlphaScope.

  export: read the SQLite file (read-only) and write gzip JSONL files; standard library only.
  import: load an export bundle into Postgres. Idempotent: every table is upserted, imported leaderboard runs
          are replaced, so running it twice leaves the same rows.

Traders keep the history state the old collector reached (complete histories are not fetched again).
"""

import gzip
import json
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.okx.positions import normalize, parse_inst
from app.traders import okx_key

log = logging.getLogger(__name__)

FILES = ("traders", "positions", "lb_entries", "daily_runs", "daily_members", "daily_obs", "fav_groups", "favorites",
         "candles")
WINDOWS = ("7d", "30d", "90d", "365d")
BAR_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d"}
HISTORY_STATUSES = {"partial", "target_complete", "source_complete", "error_backoff"}
POOL_STATUSES = {"active", "watching", "dropped", "inactive", "error_backoff"}
BATCH = 5000


# --- export (SQLite -> JSONL.gz) ---

EXPORT_SQL = {
    "traders": """SELECT author_id, unique_name, nick_name, portrait, history_status, oldest_u_time, newest_u_time,
                         target_days, last_cursor_after, failure_count, last_history_error, pool_status, pool_priority,
                         first_discovered_at, first_pool_source, last_seen_official_at, last_history_refresh,
                         last_profile_refresh, updated_at, profile_json FROM traders""",
    "positions": "SELECT author_id, unique_name, raw_json FROM positions",
    "lb_entries": """SELECT window, author_id, rank_order, first_seen_at, last_seen_at, seen_days_count,
                            consecutive_seen_days, absent_count FROM leaderboard_entries WHERE sort_by = 'pnl_ratio'""",
    "daily_runs": """SELECT id, business_date, started_at, completed_at, expected_pages, expected_total, unique_count,
                            source_version FROM daily_leaderboard_runs WHERE status = 'complete'""",
    "daily_members": """SELECT m.run_id, m.author_id, m.official_rank, m.asset, m.payload_json
                        FROM daily_leaderboard_members m JOIN daily_leaderboard_runs r ON r.id = m.run_id
                        WHERE r.status = 'complete'""",
    "daily_obs": "SELECT author_id, first_seen_at, last_seen_at, seen_days, streak, currently_seen "
                 "FROM daily_leaderboard_observations",
    "fav_groups": "SELECT id, name, sort_order FROM favorite_groups",
    "favorites": "SELECT author_id, group_id, note, created_at FROM favorites",
    "candles": "SELECT inst_id, bar, ts, open, high, low, close, vol FROM candles WHERE confirm = 1",
}


def export_sqlite(sqlite_path: Path, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts = {}
    try:
        for name in FILES:
            n = 0
            with gzip.open(out_dir / f"{name}.jsonl.gz", "wt", encoding="utf-8") as f:
                for row in conn.execute(EXPORT_SQL[name]):
                    f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    n += 1
            counts[name] = n
            log.info("exported %s: %d", name, n)
    finally:
        conn.close()
    (out_dir / "manifest.json").write_text(json.dumps({"source": str(sqlite_path), "counts": counts,
                                                       "exported_at": datetime.now(UTC).isoformat()}, indent=1))
    return counts


# --- import (JSONL.gz -> Postgres) ---


def _rows(bundle: Path, name: str) -> Iterator[dict]:
    with gzip.open(bundle / f"{name}.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def _ts(v) -> datetime | None:
    """Old collector timestamps are epoch seconds, a few columns epoch milliseconds."""
    if not v:
        return None
    v = int(v)
    return datetime.fromtimestamp(v / 1000 if v > 10**11 else v, UTC)


def _batches(it, n: int = BATCH):
    batch = []
    for x in it:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def _import_traders(s: Session, bundle: Path, now: datetime) -> dict[str, int]:
    wallets, states = [], []
    for t in _rows(bundle, "traders"):
        aid = str(t["author_id"])
        seen = _ts(t["first_discovered_at"]) or _ts(t["updated_at"]) or now
        wallets.append({"a": okx_key(aid), "uid": aid, "h": t["unique_name"] or None, "n": t["nick_name"],
                        "av": t["portrait"], "fs": seen, "ls": _ts(t["last_seen_official_at"]) or seen})
        try:
            profile = json.loads(t["profile_json"]) if t["profile_json"] else None
        except ValueError:
            profile = None
        states.append({"uid": aid,
                       "hs": t["history_status"] if t["history_status"] in HISTORY_STATUSES else "partial",
                       "ps": t["pool_status"] if t["pool_status"] in POOL_STATUSES else "inactive",
                       "pr": t["pool_priority"] if t["pool_priority"] is not None else 3,
                       "td": t["target_days"] or 365, "cur": t["last_cursor_after"] or None,
                       "fc": t["failure_count"] or 0, "err": t["last_history_error"] or None,
                       "hsync": _ts(t["last_history_refresh"]), "psync": _ts(t["last_profile_refresh"]),
                       "src": (t["first_pool_source"] or None) and t["first_pool_source"][:32],
                       "seen": _ts(t["last_seen_official_at"]), "profile": json.dumps(profile) if profile else None,
                       "now": now})
    s.execute(text("""
        INSERT INTO wallet (address, venue, venue_uid, handle, display_name, avatar_url, first_seen_at, first_seen_source,
                            last_seen_at, present_in_latest)
        VALUES (:a, 'okx', :uid, :h, :n, :av, :fs, 'okx_import', :ls, false)
        ON CONFLICT (address) DO UPDATE SET handle = coalesce(EXCLUDED.handle, wallet.handle),
          display_name = coalesce(EXCLUDED.display_name, wallet.display_name),
          avatar_url = coalesce(EXCLUDED.avatar_url, wallet.avatar_url),
          first_seen_at = least(wallet.first_seen_at, EXCLUDED.first_seen_at),
          last_seen_at = greatest(wallet.last_seen_at, EXCLUDED.last_seen_at)"""), wallets)
    ids = {uid: wid for wid, uid in s.execute(text("SELECT id, venue_uid FROM wallet WHERE venue = 'okx'")).all()}
    for st in states:
        st["w"] = ids[st["uid"]]
    # The live collector's state wins over the imported one once it exists (keep the later sync time).
    s.execute(text("""
        INSERT INTO okx_trader (wallet_id, history_status, pool_status, priority, target_days, cursor_after,
          failure_count, last_error, history_synced_at, profile_synced_at, first_pool_source, last_seen_official_at,
          profile, n_positions, updated_at)
        VALUES (:w, :hs, :ps, :pr, :td, :cur, :fc, :err, :hsync, :psync, :src, :seen, CAST(:profile AS jsonb), 0, :now)
        ON CONFLICT (wallet_id) DO UPDATE SET
          history_status = CASE WHEN okx_trader.history_synced_at >= EXCLUDED.history_synced_at
                                THEN okx_trader.history_status ELSE EXCLUDED.history_status END,
          cursor_after = CASE WHEN okx_trader.history_synced_at >= EXCLUDED.history_synced_at
                              THEN okx_trader.cursor_after ELSE EXCLUDED.cursor_after END,
          history_synced_at = greatest(okx_trader.history_synced_at, EXCLUDED.history_synced_at),
          first_pool_source = coalesce(okx_trader.first_pool_source, EXCLUDED.first_pool_source),
          profile = coalesce(okx_trader.profile, EXCLUDED.profile), updated_at = EXCLUDED.updated_at"""), states)
    return ids


def _import_positions(s: Session, bundle: Path, ids: dict[str, int], now: datetime) -> dict:
    by_name = {h: wid for wid, h in s.execute(text(
        "SELECT id, handle FROM wallet WHERE venue = 'okx' AND handle IS NOT NULL")).all()}
    cols = ["wallet_id", "pos_id", "inst_id", "coin", "ct_type", "direction", "open_time", "close_time", "open_avg_px",
            "close_avg_px", "max_size", "closed_size", "max_notional", "pnl", "realized_pnl", "fee", "funding",
            "pnl_ratio", "lever", "mgn_mode", "close_type", "liquidated", "raw", "fetched_at"]
    s.execute(text("CREATE TEMP TABLE tmp_okx_pos (LIKE okx_position INCLUDING DEFAULTS) ON COMMIT DROP"))
    pg = s.connection().connection.driver_connection
    stats = {"rows": 0, "skipped_unknown_trader": 0, "skipped_invalid": 0}
    seen: set[tuple[int, str]] = set()
    with pg.cursor() as cur, cur.copy(f"COPY tmp_okx_pos ({', '.join(cols)}) FROM STDIN") as copy:
        for r in _rows(bundle, "positions"):
            wid = ids.get(str(r["author_id"])) if r["author_id"] else None
            wid = wid or by_name.get(r["unique_name"])
            if wid is None:
                stats["skipped_unknown_trader"] += 1
                continue
            try:
                raw = json.loads(r["raw_json"])
                if raw.get("instType", "SWAP") != "SWAP":
                    raise ValueError("not a swap")
                p = normalize(raw)
            except (ValueError, KeyError, ArithmeticError):
                stats["skipped_invalid"] += 1
                continue
            if (wid, p["pos_id"]) in seen:  # the old table keyed on uniqueName; one trader, one copy
                continue
            seen.add((wid, p["pos_id"]))
            copy.write_row([wid, *[p[c] for c in cols[1:-2]], json.dumps(raw), now])
            stats["rows"] += 1
    s.execute(text(f"""
        INSERT INTO okx_position ({', '.join(cols)}) SELECT {', '.join(cols)} FROM tmp_okx_pos
        ON CONFLICT (wallet_id, pos_id) DO UPDATE SET close_time = EXCLUDED.close_time,
          close_avg_px = EXCLUDED.close_avg_px, max_size = EXCLUDED.max_size, closed_size = EXCLUDED.closed_size,
          max_notional = EXCLUDED.max_notional, pnl = EXCLUDED.pnl, realized_pnl = EXCLUDED.realized_pnl,
          fee = EXCLUDED.fee, funding = EXCLUDED.funding, close_type = EXCLUDED.close_type,
          liquidated = EXCLUDED.liquidated, raw = EXCLUDED.raw
        WHERE okx_position.close_time <= EXCLUDED.close_time"""))
    s.execute(text("""
        UPDATE okx_trader t SET n_positions = p.n, oldest_at = p.oldest, newest_at = p.newest
        FROM (SELECT wallet_id, count(*) AS n, min(close_time) AS oldest, max(close_time) AS newest
              FROM okx_position GROUP BY wallet_id) p
        WHERE p.wallet_id = t.wallet_id"""))
    return stats


def _import_leaderboards(s: Session, bundle: Path, ids: dict[str, int]) -> dict:
    s.execute(text("DELETE FROM okx_leaderboard_run WHERE source = 'sqlite_import'"))
    run_ids: dict[str, int] = {}
    for r in _rows(bundle, "daily_runs"):
        run_ids[r["id"]] = s.execute(text("""
            INSERT INTO okx_leaderboard_run (business_date, time_window, status, data_version, expected_pages,
              expected_total, pages_done, unique_count, new_traders, started_at, completed_at, source)
            VALUES (:d, '7d', 'complete', :ver, :ep, :et, :ep, :uc, 0, :sa, :ca, 'sqlite_import') RETURNING id"""),
            {"d": date.fromisoformat(r["business_date"]), "ver": (r["source_version"] or None),
             "ep": r["expected_pages"], "et": r["expected_total"], "uc": r["unique_count"],
             "sa": _ts(r["started_at"]), "ca": _ts(r["completed_at"])}).scalar_one()
    members = 0
    for batch in _batches(_rows(bundle, "daily_members")):
        rows = []
        for m in batch:
            wid, rid = ids.get(str(m["author_id"])), run_ids.get(m["run_id"])
            if wid is None or rid is None:
                continue
            p = json.loads(m["payload_json"] or "{}")
            pct = lambda k: float(p[k]) / 100 if p.get(k) not in (None, "") else None  # noqa: E731
            rows.append({"r": rid, "w": wid, "rank": m["official_rank"], "asset": m["asset"], "pnl": p.get("pnl"),
                         "ratio": p.get("pnlRatio"), "win": pct("officialWinRate"), "dd": pct("maxDrawdownPct"),
                         "rates": json.dumps([[x.get("statTime"), x.get("value")] for x in p.get("rates") or []])})
        if rows:
            s.execute(text("""
                INSERT INTO okx_leaderboard_row (run_id, wallet_id, rank, asset, pnl, pnl_ratio, win_rate, max_drawdown,
                  rates)
                VALUES (:r, :w, :rank, :asset, :pnl, :ratio, :win, :dd, CAST(:rates AS jsonb))
                ON CONFLICT DO NOTHING"""), rows)
            members += len(rows)

    presence: dict[tuple[int, str], dict] = {}
    for e in _rows(bundle, "lb_entries"):
        wid = ids.get(str(e["author_id"]))
        if wid is None or e["window"] not in WINDOWS or not e["first_seen_at"]:
            continue
        presence[(wid, e["window"])] = {
            "w": wid, "win": e["window"], "fs": _ts(e["first_seen_at"]), "ls": _ts(e["last_seen_at"] or e["first_seen_at"]),
            "days": e["seen_days_count"] or 0, "streak": e["consecutive_seen_days"] or 0,
            "cur": not e["absent_count"], "rank": e["rank_order"]}
    for o in _rows(bundle, "daily_obs"):  # complete daily 7d runs are the better source for the 7d window
        wid = ids.get(str(o["author_id"]))
        if wid is None:
            continue
        prev = presence.get((wid, "7d"), {})
        presence[(wid, "7d")] = {"w": wid, "win": "7d", "fs": _ts(o["first_seen_at"]), "ls": _ts(o["last_seen_at"]),
                                 "days": o["seen_days"], "streak": o["streak"], "cur": bool(o["currently_seen"]),
                                 "rank": prev.get("rank")}
    if presence:
        s.execute(text("""
            INSERT INTO okx_presence (wallet_id, time_window, first_seen_at, last_seen_at, seen_days, streak,
              currently_seen, last_rank, best_rank)
            VALUES (:w, :win, :fs, :ls, :days, :streak, :cur, :rank, :rank)
            ON CONFLICT (wallet_id, time_window) DO UPDATE SET
              first_seen_at = least(okx_presence.first_seen_at, EXCLUDED.first_seen_at),
              last_seen_at = greatest(okx_presence.last_seen_at, EXCLUDED.last_seen_at),
              seen_days = greatest(okx_presence.seen_days, EXCLUDED.seen_days)"""), list(presence.values()))
    s.execute(text("""
        UPDATE okx_presence p SET best_rank = least(p.best_rank, b.best)
        FROM (SELECT r.wallet_id, min(r.rank) AS best FROM okx_leaderboard_row r
              JOIN okx_leaderboard_run u ON u.id = r.run_id WHERE u.time_window = '7d' GROUP BY r.wallet_id) b
        WHERE p.wallet_id = b.wallet_id AND p.time_window = '7d'"""))
    return {"runs": len(run_ids), "members": members, "presence": len(presence)}


def _import_favorites(s: Session, bundle: Path, ids: dict[str, int]) -> int:
    groups: dict[str, int] = {}
    for g in _rows(bundle, "fav_groups"):
        name = f"OKX·{g['name']}"[:64]
        groups[g["id"]] = s.execute(text("""
            INSERT INTO watch_group (owner, name, sort) VALUES ('default', :n, :s)
            ON CONFLICT (owner, name) DO UPDATE SET name = EXCLUDED.name RETURNING id"""),
            {"n": name, "s": 100 + (g["sort_order"] or 0)}).scalar_one()
    n = 0
    for f in _rows(bundle, "favorites"):
        wid, gid = ids.get(str(f["author_id"])), groups.get(f["group_id"])
        if wid is None or gid is None:
            continue
        n += s.execute(text("""
            INSERT INTO watch_item (group_id, wallet_id, note, tags, created_at)
            VALUES (:g, :w, :note, ARRAY['okx'], :t) ON CONFLICT (group_id, wallet_id) DO NOTHING"""),
            {"g": gid, "w": wid, "note": f["note"] or None, "t": _ts(f["created_at"]) or datetime.now(UTC)}).rowcount
        s.execute(text("""
            UPDATE okx_trader SET priority = 0,
              pool_status = CASE WHEN pool_status IN ('dropped', 'inactive') THEN 'watching' ELSE pool_status END
            WHERE wallet_id = :w"""), {"w": wid})
    return n


def _import_candles(s: Session, bundle: Path, now: datetime) -> int:
    s.execute(text("""CREATE TEMP TABLE tmp_okx_candle (coin varchar(32), interval varchar(8), t timestamptz,
                      o numeric, h numeric, l numeric, c numeric, v numeric) ON COMMIT DROP"""))
    pg = s.connection().connection.driver_connection
    n = 0
    with pg.cursor() as cur, cur.copy("COPY tmp_okx_candle FROM STDIN") as copy:
        for r in _rows(bundle, "candles"):
            iv = BAR_INTERVALS.get(r["bar"])
            if iv is None:
                continue
            try:
                coin, ct_type = parse_inst(r["inst_id"])
            except ValueError:
                continue
            if ct_type != "linear" or "-USDT-" not in r["inst_id"]:
                continue  # one price series per coin: the USDT-margined swap
            copy.write_row([coin, iv, _ts(r["ts"]), r["open"], r["high"], r["low"], r["close"], r["vol"] or 0])
            n += 1
    s.execute(text("""
        INSERT INTO market_candle (source, coin, interval, t, o, h, l, c, v, fetched_at)
        SELECT DISTINCT ON (coin, interval, t) 'okx', coin, interval, t, o, h, l, c, v, :now FROM tmp_okx_candle
        ORDER BY coin, interval, t
        ON CONFLICT DO NOTHING"""), {"now": now})
    return n


def import_bundle(sf: sessionmaker[Session], bundle: Path, derive: bool = True) -> dict:
    now = datetime.now(UTC)
    out: dict = {}
    with sf() as s:
        ids = _import_traders(s, bundle, now)
        s.commit()
    out["traders"] = len(ids)
    log.info("traders: %d", len(ids))
    with sf() as s:
        out["positions"] = _import_positions(s, bundle, ids, now)
        s.commit()
    log.info("positions: %s", out["positions"])
    with sf() as s:
        out["leaderboards"] = _import_leaderboards(s, bundle, ids)
        out["favorites"] = _import_favorites(s, bundle, ids)
        s.commit()
    log.info("leaderboards: %s, favorites: %d", out["leaderboards"], out["favorites"])
    with sf() as s:
        out["candles"] = _import_candles(s, bundle, now)
        s.commit()
    log.info("candles: %d", out["candles"])
    if derive:
        out["derived"] = derive_all(sf)
    return out


def derive_all(sf: sessionmaker[Session], only_stale: bool = False) -> dict:
    """Derive cycles and decisions for every OKX trader with positions (or only those changed since)."""
    from app.okx.positions import derive_wallet

    with sf() as s:
        cond = "AND (t.derived_at IS NULL OR t.derived_at < t.history_synced_at)" if only_stale else ""
        wids = s.execute(text(f"""
            SELECT t.wallet_id FROM okx_trader t WHERE t.n_positions > 0 {cond} ORDER BY t.wallet_id""")).scalars().all()
    totals = {"wallets": 0, "cycles": 0, "decisions": 0}
    for i, wid in enumerate(wids):
        with sf() as s:
            r = derive_wallet(s, wid)
            s.commit()
        totals["wallets"] += 1
        totals["cycles"] += r["cycles"]
        totals["decisions"] += r["decisions"]
        if (i + 1) % 200 == 0:
            log.info("derived %d/%d wallets", i + 1, len(wids))
    return totals
