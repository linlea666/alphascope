"""Price-zone research: which archived wallets opened longs / shorts inside a price zone and time range,
and how those positions ended. Intersections answer "who bought the lows AND sold the highs".

Only wallets with archived fills are covered, so every response states how many archived wallets
actually cover the queried period.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import SYNC_STATUS_SQL, VenueFilter, data_as_of, num, venue_clause
from app.db import get_session

router = APIRouter(prefix="/api/v1/zones", tags=["zones"])

# Current BTC cycle (see docs/08): bottom 57,768 on 7/1, top 87,471 on 9/21 and 87,235 on 10/2.
PRESETS = {
    "btc_low_long": {"coin": "BTC", "side": "long", "px_min": 57000, "px_max": 69999,
                     "start": "2026-07-01", "end": "2026-09-30"},
    "btc_high_short": {"coin": "BTC", "side": "short", "px_min": 84000, "px_max": 88000,
                       "start": "2026-09-01", "end": "2026-10-31"},
}
PRESET_PAIRS = {"btc_both": ("btc_low_long", "btc_high_short")}

ZONE_SQL = """
    SELECT d.wallet_id, count(*) AS decisions, sum(d.notional) AS notional,
           sum(d.vwap * d.size) / nullif(sum(d.size), 0) AS avg_px, min(d.started_at) AS first_at,
           max(d.started_at) AS last_at, array_agg(DISTINCT d.cycle_id) AS cycle_ids
    FROM trade_decision d JOIN wallet w ON w.id = d.wallet_id
    WHERE d.coin = :coin AND d.started_at >= :start AND d.started_at < :end {venue}
      AND d.vwap BETWEEN :px_min AND :px_max AND d.kind IN ('open', 'add', 'flip')
      AND CASE WHEN :side = 'long' THEN d.pos_after > 0 ELSE d.pos_after < 0 END
    GROUP BY d.wallet_id"""


def _params(coin: str, side: str, px_min: float, px_max: float, start: date, end: date) -> dict:
    if px_min > px_max or start > end:
        raise HTTPException(422, "empty price or date range")
    return {"coin": coin, "side": side, "px_min": px_min, "px_max": px_max, "start": start,
            "end": date.fromordinal(end.toordinal() + 1)}


def _zone(session: Session, p: dict, venue: str = "all") -> dict[int, dict]:
    return {r["wallet_id"]: dict(r) for r in session.execute(text(ZONE_SQL.format(venue=venue_clause(venue))),
                                                             p).mappings().all()}


def _outcomes(session: Session, cycle_ids: list[int], coin: str) -> dict[int, dict]:
    """Per cycle: status, entry/exit, net realized PnL and current size (for open cycles)."""
    if not cycle_ids:
        return {}
    # one indexed lookup per source (the primary key leads with source)
    last_px = None
    for source in ("hyperliquid", "binance_um"):
        last_px = session.execute(text("""
            SELECT c FROM market_candle WHERE source = :src AND coin = :coin AND interval = '1h'
            ORDER BY t DESC LIMIT 1"""), {"src": source, "coin": coin}).scalar()
        if last_px is not None:
            break
    rows = session.execute(text("""
        SELECT c.id, c.wallet_id, c.direction, c.status, c.opened_at, c.closed_at, c.entry_px, c.exit_px,
               c.realized_pnl - c.fees AS net, c.holding_hours, {sync} AS sync_status,
               (SELECT d.pos_after FROM trade_decision d WHERE d.cycle_id = c.id ORDER BY d.ended_at DESC LIMIT 1) AS size
        FROM position_cycle c LEFT JOIN fill_sync f ON f.wallet_id = c.wallet_id
        LEFT JOIN okx_trader o ON o.wallet_id = c.wallet_id WHERE c.id = ANY(:ids)""".format(sync=SYNC_STATUS_SQL)),
        {"ids": cycle_ids}).mappings().all()
    out = {}
    for r in rows:
        status = r["status"]
        if status == "open" and r["sync_status"] != "synced":
            status = "data_end"
        unrealized = None
        if status == "open" and last_px is not None and r["size"] is not None and r["entry_px"] is not None:
            unrealized = float(r["size"]) * (float(last_px) - float(r["entry_px"]))
        out[r["id"]] = {"status": status, "direction": r["direction"], "opened_at": r["opened_at"],
                        "closed_at": r["closed_at"], "entry_px": num(r["entry_px"]), "exit_px": num(r["exit_px"]),
                        "net": num(r["net"]), "unrealized": unrealized, "holding_hours": num(r["holding_hours"])}
    return out


def _coverage(session: Session, start: date, venue: str = "all") -> dict:
    """Archived traders whose history reaches back to the start of the queried period (fills or OKX positions)."""
    hl = session.execute(text("""
        SELECT count(*) FILTER (WHERE n_fills > 0) AS archived,
               count(*) FILTER (WHERE n_fills > 0 AND (earliest_fill_at <= :s OR (NOT coalesce(truncated, false)
                                AND requested_start <= :s))) AS covering,
               count(*) AS queued_total
        FROM fill_sync"""), {"s": start}).mappings().one() if venue != "okx" else {}
    okx = session.execute(text("""
        SELECT count(*) FILTER (WHERE n_positions > 0) AS archived,
               count(*) FILTER (WHERE n_positions > 0 AND (oldest_at <= :s OR history_status = 'source_complete'))
                 AS covering,
               count(*) AS queued_total
        FROM okx_trader"""), {"s": start}).mappings().one() if venue != "hyperliquid" else {}
    return {k: (hl.get(k) or 0) + (okx.get(k) or 0) for k in ("archived", "covering", "queued_total")}


def _summarise(z: dict, outcomes: dict[int, dict]) -> dict:
    cyc = [outcomes[c] for c in z["cycle_ids"] if c in outcomes]
    closed = [c for c in cyc if c["status"] in ("closed", "flipped")]
    exits = [c["exit_px"] for c in closed if c["exit_px"]]
    return {
        "decisions": z["decisions"], "notional": num(z["notional"]), "avg_px": num(z["avg_px"]),
        "first_at": z["first_at"], "last_at": z["last_at"], "cycles": len(cyc),
        "closed": len(closed), "open": sum(1 for c in cyc if c["status"] == "open"),
        "data_end": sum(1 for c in cyc if c["status"] == "data_end"),
        "realized_net": sum(c["net"] or 0 for c in cyc), "unrealized": sum(c["unrealized"] or 0 for c in cyc),
        "avg_exit_px": sum(exits) / len(exits) if exits else None,
        "max_holding_hours": max((c["holding_hours"] or 0 for c in cyc), default=None),
    }


def _wallet_rows(session: Session, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = session.execute(text("""
        SELECT w.id, w.address, w.display_name, w.venue, w.account_value, w.tier,
               w.quality_flags || w.enrichment_flags AS flags, f.earliest_fill_at, f.truncated, f.status AS sync_status,
               (SELECT edge_shrunk FROM timing_score t WHERE t.wallet_id = w.id AND t.scale = 'macro'
                  AND t.time_window = 'all' AND t.coin_group = 'all' AND t.status = 'scored') AS macro_edge,
               (SELECT q_value FROM timing_score t WHERE t.wallet_id = w.id AND t.scale = 'macro'
                  AND t.time_window = 'all' AND t.coin_group = 'all' AND t.status = 'scored') AS macro_q,
               o.asset AS okx_asset
        FROM wallet w LEFT JOIN fill_sync f ON f.wallet_id = w.id LEFT JOIN okx_trader o ON o.wallet_id = w.id
        WHERE w.id = ANY(:ids)"""),
        {"ids": ids}).mappings().all()
    return {r["id"]: {**{k: r[k] for k in ("address", "display_name", "venue", "tier", "earliest_fill_at",
                                           "truncated", "sync_status")},
                      "account_value": num(r["account_value"]), "okx_asset": num(r["okx_asset"]),
                      "flags": list(r["flags"] or []),
                      "macro_edge": num(r["macro_edge"]), "macro_q": num(r["macro_q"])} for r in rows}


@router.get("/presets")
def presets():
    return {"presets": PRESETS, "pairs": {k: list(v) for k, v in PRESET_PAIRS.items()}}


@router.get("")
def zone_query(
    session: Session = Depends(get_session),
    preset: str | None = Query(None, description="btc_low_long | btc_high_short | btc_both"),
    coin: str = "BTC",
    side: Literal["long", "short"] = "long",
    px_min: float = Query(0, ge=0),
    px_max: float = Query(1e12, ge=0),
    start: date = date(2026, 7, 1),
    end: date = date(2026, 12, 31),
    also_side: Literal["long", "short"] | None = None,
    also_px_min: float | None = Query(None, ge=0),
    also_px_max: float | None = Query(None, ge=0),
    also_start: date | None = None,
    also_end: date | None = None,
    sort: Literal["notional", "realized", "earliest"] = "notional",
    limit: int = Query(100, ge=1, le=500),
    venue: VenueFilter = "all",
):
    first: dict
    second: dict | None = None
    if preset:
        if preset in PRESETS:
            first = PRESETS[preset]
        elif preset in PRESET_PAIRS:
            a, b = PRESET_PAIRS[preset]
            first, second = PRESETS[a], PRESETS[b]
        else:
            raise HTTPException(422, f"unknown preset; choose from {sorted([*PRESETS, *PRESET_PAIRS])}")
        first = {**first, "start": date.fromisoformat(first["start"]), "end": date.fromisoformat(first["end"])}
        if second:
            second = {**second, "start": date.fromisoformat(second["start"]), "end": date.fromisoformat(second["end"])}
    else:
        first = {"coin": coin, "side": side, "px_min": px_min, "px_max": px_max, "start": start, "end": end}
        if also_side:
            second = {"coin": coin, "side": also_side, "px_min": also_px_min or 0, "px_max": also_px_max or 1e12,
                      "start": also_start or start, "end": also_end or end}

    z1 = _zone(session, _params(**first), venue)
    z2 = _zone(session, _params(**second), venue) if second else None
    ids = [w for w in z1 if z2 is None or w in z2]
    outcomes = _outcomes(session, [c for w in ids for c in z1[w]["cycle_ids"]]
                         + ([c for w in ids for c in z2[w]["cycle_ids"]] if z2 else []), first["coin"])
    meta = _wallet_rows(session, ids)
    rows = []
    for w in ids:
        row = {**meta.get(w, {}), "zone": _summarise(z1[w], outcomes)}
        if z2 is not None:
            row["also"] = _summarise(z2[w], outcomes)
        rows.append(row)
    key = {
        "notional": lambda r: -(r["zone"]["notional"] or 0),
        "realized": lambda r: -((r["zone"]["realized_net"] or 0) + (r.get("also", {}).get("realized_net") or 0)),
        "earliest": lambda r: r["zone"]["first_at"],
    }[sort]
    rows.sort(key=key)
    return {
        "freshness": data_as_of(session),
        "query": {"first": {**first, "start": str(first["start"]), "end": str(first["end"])},
                  "second": {**second, "start": str(second["start"]), "end": str(second["end"])} if second else None},
        "coverage": _coverage(session, first["start"], venue),
        "total": len(rows),
        "rows": rows[:limit],
    }
