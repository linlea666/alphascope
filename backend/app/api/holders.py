"""Swing holders: wallets that entered in one price zone and exited (or are still holding) in another,
plus per-leg capture rankings. Built on position cycles (entry/exit legs per decision) and leg_capture."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import SYNC_STATUS_SQL, VenueFilter, data_as_of, num, venue_clause
from app.db import get_session

router = APIRouter(prefix="/api/v1/holders", tags=["holders"])

# Current BTC cycle: bottom 57,768 (7/1), top 87,471 (9/21) / 87,235 (10/2), low 61,970 (11/15).
PRESETS = {
    "btc_long_hold": {"coin": "BTC", "direction": "long", "entry_min": 63000, "entry_max": 68000,
                      "exit_min": 83000, "exit_max": 87500, "start": "2026-07-01", "end": "2026-11-30"},
    "btc_short_hold": {"coin": "BTC", "direction": "short", "entry_min": 82000, "entry_max": 87500,
                       "exit_min": 62000, "exit_max": 72000, "start": "2026-09-01", "end": "2026-12-31"},
}
MIN_SHARE = 0.5  # at least half of the peak position exited inside the exit zone (or still held there)

CYCLE_SQL = """
    SELECT c.id, c.wallet_id, c.coin, c.direction, c.opened_at, c.closed_at, c.status, c.complete, c.entry_px,
           c.exit_px, c.max_size, c.max_notional, c.realized_pnl - c.fees AS net, c.holding_hours,
           {sync} AS sync_status,
           (SELECT coalesce(sum((e->>2)::numeric), 0) FROM jsonb_array_elements(coalesce(nullif(c.exits, 'null'::jsonb), '[]')) e
              WHERE (e->>1)::numeric BETWEEN :xmin AND :xmax) AS exit_in_zone,
           (SELECT sum((e->>2)::numeric * (e->>1)::numeric) / nullif(sum((e->>2)::numeric), 0)
              FROM jsonb_array_elements(coalesce(nullif(c.exits, 'null'::jsonb), '[]')) e
              WHERE (e->>1)::numeric BETWEEN :xmin AND :xmax) AS zone_exit_px,
           (SELECT min((e->>0)::timestamptz) FROM jsonb_array_elements(coalesce(nullif(c.exits, 'null'::jsonb), '[]')) e
              WHERE (e->>1)::numeric BETWEEN :xmin AND :xmax) AS first_zone_exit_at,
           (SELECT coalesce(sum((e->>2)::numeric), 0) FROM jsonb_array_elements(coalesce(nullif(c.entries, 'null'::jsonb), '[]')) e)
             - (SELECT coalesce(sum((e->>2)::numeric), 0) FROM jsonb_array_elements(coalesce(nullif(c.exits, 'null'::jsonb), '[]')) e)
             AS remaining
    FROM position_cycle c JOIN wallet w ON w.id = c.wallet_id
    LEFT JOIN fill_sync f ON f.wallet_id = c.wallet_id LEFT JOIN okx_trader o ON o.wallet_id = c.wallet_id
    WHERE c.coin = :coin AND c.direction = :direction AND c.entry_px BETWEEN :emin AND :emax
      AND c.opened_at >= :start AND c.opened_at < :end AND (f.wallet_id IS NOT NULL OR o.wallet_id IS NOT NULL)
      {venue}"""


def _last_price(session: Session, coin: str) -> float | None:
    for source in ("hyperliquid", "binance_um"):
        px = session.execute(text("""
            SELECT c FROM market_candle WHERE source = :src AND coin = :coin AND interval = '1h'
            ORDER BY t DESC LIMIT 1"""), {"src": source, "coin": coin}).scalar()
        if px is not None:
            return float(px)
    return None


@router.get("/presets")
def presets():
    return {"presets": PRESETS, "min_share": MIN_SHARE}


@router.get("/zones")
def zone_holders(
    session: Session = Depends(get_session),
    preset: str | None = None,
    coin: str = "BTC",
    direction: Literal["long", "short"] = "long",
    entry_min: float = Query(0, ge=0),
    entry_max: float = Query(1e12, ge=0),
    exit_min: float = Query(0, ge=0),
    exit_max: float = Query(1e12, ge=0),
    start: date = date(2026, 7, 1),
    end: date = date(2026, 12, 31),
    min_share: float = Query(MIN_SHARE, ge=0, le=1),
    include_near: bool = Query(False, description="also list cycles that entered in zone but did not qualify"),
    venue: VenueFilter = "all",
):
    if preset:
        if preset not in PRESETS:
            raise HTTPException(422, f"unknown preset; choose from {sorted(PRESETS)}")
        q = PRESETS[preset]
        coin, direction = q["coin"], q["direction"]
        entry_min, entry_max, exit_min, exit_max = q["entry_min"], q["entry_max"], q["exit_min"], q["exit_max"]
        start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
    if entry_min > entry_max or exit_min > exit_max or start > end:
        raise HTTPException(422, "empty range")
    last_px = _last_price(session, coin)
    rows = session.execute(text(CYCLE_SQL.format(sync=SYNC_STATUS_SQL, venue=venue_clause(venue))), {
        "coin": coin, "direction": direction, "emin": entry_min, "emax": entry_max, "xmin": exit_min,
        "xmax": exit_max, "start": start, "end": date.fromordinal(end.toordinal() + 1)}).mappings().all()
    sign = 1 if direction == "long" else -1
    cycles = []
    for r in rows:
        max_size = float(r["max_size"]) or 0.0
        exited_share = float(r["exit_in_zone"]) / max_size if max_size else 0.0
        status = r["status"]
        if status == "open" and r["sync_status"] != "synced":
            status = "data_end"
        remaining = max(float(r["remaining"] or 0), 0.0)
        holding_share = remaining / max_size if status == "open" and max_size else 0.0
        in_zone_now = last_px is not None and exit_min <= last_px <= exit_max
        qualifies = exited_share >= min_share or (status == "open" and in_zone_now and holding_share >= min_share)
        if not qualifies and not include_near:
            continue
        entry = float(r["entry_px"])
        target_px = num(r["zone_exit_px"]) if exited_share >= min_share else (last_px if qualifies else None)
        cycles.append({
            "cycle_id": r["id"], "wallet_id": r["wallet_id"], "qualifies": qualifies, "status": status,
            "complete": r["complete"], "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            "first_zone_exit_at": r["first_zone_exit_at"], "entry_px": entry, "exit_px": num(r["exit_px"]),
            "zone_exit_px": num(r["zone_exit_px"]), "exited_in_zone_share": exited_share,
            "holding_share": holding_share, "max_notional": num(r["max_notional"]), "net": num(r["net"]),
            "unrealized": sign * remaining * (last_px - entry) if status == "open" and last_px else None,
            "move_captured": sign * (target_px - entry) / entry if target_px else None,
            "holding_days": float(r["holding_hours"]) / 24,
        })
    ids = sorted({c["wallet_id"] for c in cycles})
    meta = {}
    if ids:
        for m in session.execute(text("""
            SELECT id, address, display_name, account_value, quality_flags || enrichment_flags AS flags
            FROM wallet WHERE id = ANY(:ids)"""), {"ids": ids}).mappings().all():
            meta[m["id"]] = {"address": m["address"], "display_name": m["display_name"],
                             "account_value": num(m["account_value"]), "flags": list(m["flags"] or [])}
    for c in cycles:
        c.update(meta.get(c.pop("wallet_id"), {}))
        av = c.get("account_value")
        c["size_vs_equity"] = (c["max_notional"] / av) if av and c["max_notional"] else None
    cycles.sort(key=lambda c: (not c["qualifies"], -(c["move_captured"] or -1), -(c["max_notional"] or 0)))
    coverage = session.execute(text("""
        SELECT count(*) FILTER (WHERE n_fills > 0) AS archived,
               count(*) FILTER (WHERE n_fills > 0 AND (earliest_fill_at <= :s OR (NOT coalesce(truncated, false)
                                AND requested_start <= :s))) AS covering,
               count(*) AS queued_total FROM fill_sync"""), {"s": start}).mappings().one()
    return {
        "freshness": data_as_of(session),
        "query": {"coin": coin, "direction": direction, "entry_min": entry_min, "entry_max": entry_max,
                  "exit_min": exit_min, "exit_max": exit_max, "start": str(start), "end": str(end),
                  "min_share": min_share},
        "last_price": last_px,
        "coverage": dict(coverage),
        "qualifying": sum(1 for c in cycles if c["qualifies"]),
        "rows": cycles[:300],
    }


@router.get("/legs")
def leg_list(session: Session = Depends(get_session), coin: str = "BTC"):
    legs = session.execute(text("""
        SELECT leg_start, leg_end, direction, leg_change, bool_and(confirmed) AS confirmed, count(*) AS wallets
        FROM leg_capture WHERE coin = :coin GROUP BY leg_start, leg_end, direction, leg_change
        ORDER BY leg_start DESC"""), {"coin": coin}).mappings().all()
    coins = session.execute(text("""
        SELECT coin, count(DISTINCT wallet_id) AS wallets FROM leg_capture GROUP BY coin ORDER BY 2 DESC LIMIT 40""")
    ).mappings().all()
    return {"coin": coin, "legs": [{**dict(l), "leg_change": num(l["leg_change"])} for l in legs],
            "coins": [dict(c) for c in coins]}


@router.get("/capture")
def leg_capture_ranking(
    session: Session = Depends(get_session),
    coin: str = "BTC",
    leg_start: str | None = None,
    min_aligned: float = Query(0.5, ge=0, le=1),
    min_notional: float = Query(10_000, ge=0),
    full_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    venue: VenueFilter = "all",
):
    if leg_start is None:
        leg_start = session.execute(text("""
            SELECT max(leg_start) FROM leg_capture WHERE coin = :coin AND leg_end < now() - interval '1 day'"""),
            {"coin": coin}).scalar()
        if leg_start is None:
            return {"freshness": data_as_of(session), "coin": coin, "leg": None, "rows": []}
    where = ["l.coin = :coin", "l.leg_start = CAST(:ls AS timestamptz)", "l.time_aligned >= :ma",
             "l.max_notional >= :mn"]
    if full_only:
        where.append("l.coverage = 'full'")
    rows = session.execute(text(f"""
        SELECT w.address, w.display_name, w.venue, w.account_value, l.leg_start, l.leg_end, l.direction, l.leg_change,
               l.confirmed, l.coverage, l.time_aligned, l.time_against, l.max_notional, l.avg_aligned_notional,
               l.pnl_mark, l.capture_ratio
        FROM leg_capture l JOIN wallet w ON w.id = l.wallet_id
        WHERE {" AND ".join(where)}{venue_clause(venue)}
        ORDER BY l.capture_ratio DESC NULLS LAST, l.pnl_mark DESC LIMIT :limit"""),
        {"coin": coin, "ls": leg_start, "ma": min_aligned, "mn": min_notional, "limit": limit}).mappings().all()
    numeric = ("account_value", "leg_change", "time_aligned", "time_against", "max_notional", "avg_aligned_notional",
               "pnl_mark", "capture_ratio")
    return {
        "freshness": data_as_of(session), "coin": coin, "leg_start": str(leg_start),
        "filters": {"min_aligned": min_aligned, "min_notional": min_notional, "full_only": full_only},
        "rows": [{**dict(r), **{k: num(r[k]) for k in numeric}} for r in rows],
    }
