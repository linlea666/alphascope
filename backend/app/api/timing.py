from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import VenueFilter, data_as_of, num, venue_clause
from app.db import get_session
from app.research.timing import EXTREME, LADDER, MAKER_HEAVY, MIN_DECISIONS, N_PERM, TIMING_VERSION, VALID_WINDOWS, WINDOWS
from app.traders import parse_trader_key

router = APIRouter(prefix="/api/v1", tags=["timing"])

Scale = Literal["scalp", "short", "swing", "macro"]
Window = Literal["7d", "30d", "90d", "all"]
Group = Literal["all", "btc_eth", "coin"]
SORTS = {
    "edge": "t.edge_shrunk DESC NULLS LAST",
    "location": "t.location DESC NULLS LAST",
    "precision": "t.precision DESC NULLS LAST",
    "recall": "t.recall DESC NULLS LAST",
    "pnl": "t.net_pnl DESC NULLS LAST",
    "q": "t.q_value ASC NULLS LAST",
    # both sides right: the weaker of "bought in the bottom 20%" and "sold in the top 20%" shares
    "two_sided": "least(t.buy_extreme, t.sell_extreme) DESC NULLS LAST",
    # contrarian: consistently buying highs / selling lows (negative edge); research only, see the page note
    "contrarian": "t.edge_shrunk ASC NULLS LAST",
}
CONTRARIAN_P = 0.95  # one-sided p is "share of random shifts doing at least as well": >= 0.95 = worse than 95%

NUMERIC = ("location", "null_mean", "edge", "edge_shrunk", "p_value", "q_value", "precision", "recall", "alignment",
           "net_pnl", "buy_location", "sell_location", "buy_extreme", "sell_extreme", "null_extreme", "maker_share")


def _clean(r) -> dict:
    d = dict(r)
    for k in NUMERIC:
        if k in d:
            d[k] = num(d[k])
    return d


@router.get("/timing/meta")
def timing_meta():
    return {"version": TIMING_VERSION, "ladder": {k: {"interval": v[0], "half_window_hours": v[1] * v[2] / 60,
                                                     "threshold_btc": v[3]} for k, v in LADDER.items()},
            "windows": list(WINDOWS), "valid_windows": VALID_WINDOWS, "min_decisions": MIN_DECISIONS,
            "permutations": N_PERM, "extreme": EXTREME, "maker_heavy": MAKER_HEAVY}


@router.get("/timing/coins")
def timing_coins(session: Session = Depends(get_session), scale: Scale = "scalp", window: Window = "30d",
                 venue: VenueFilter = "all"):
    """Coins that have per-coin scores for this scale and window (for the board's coin picker)."""
    rows = session.execute(text(f"""
        SELECT substr(t.coin_group, 3) AS coin, count(*) AS wallets FROM timing_score t JOIN wallet w ON w.id = t.wallet_id
        WHERE t.coin_group LIKE 'c:%' AND t.scale = :s AND t.time_window = :w AND t.status = 'scored'
          AND t.version = :v{venue_clause(venue)}
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 60"""), {"s": scale, "w": window, "v": TIMING_VERSION}).mappings().all()
    return {"coins": [dict(r) for r in rows]}


@router.get("/timing")
def timing_board(
    session: Session = Depends(get_session),
    scale: Scale = "swing",
    window: Window = "90d",
    group: Group = "all",
    coin: str | None = Query(None, max_length=48, description="with group=coin"),
    sort: str = "edge",
    significant_only: bool = False,
    exclude_makers: bool = Query(False, description=f"drop wallets whose resting-order share exceeds {MAKER_HEAVY}"),
    include_insufficient: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    venue: VenueFilter = "all",
):
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {sorted(SORTS)}")
    if group == "coin":
        if not coin:
            raise HTTPException(422, "coin is required with group=coin")
        group_key = f"c:{coin}"
    else:
        group_key = group
    where = ["t.scale = :scale", "t.time_window = :window", "t.coin_group = :group", "t.version = :v"]
    if exclude_makers:
        where.append(f"coalesce(t.maker_share, 0) <= {MAKER_HEAVY}")
    if not include_insufficient:
        where.append("t.status = 'scored'")
    if sort == "contrarian":
        where.append("t.edge_shrunk < 0")
        if significant_only:
            where.append(f"t.p_value >= {CONTRARIAN_P}")
    elif significant_only:
        where.append("t.q_value < 0.1")
    base = f"FROM timing_score t JOIN wallet w ON w.id = t.wallet_id WHERE {' AND '.join(where)}{venue_clause(venue)}"
    params = {"scale": scale, "window": window, "group": group_key, "v": TIMING_VERSION}
    total = session.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = session.execute(text(f"""
        SELECT w.address, w.display_name, w.venue, w.account_value, w.tier,
               w.quality_flags || w.enrichment_flags AS flags, t.status, t.as_of, t.n_decisions, t.n_scored, t.location, t.null_mean, t.edge, t.edge_shrunk,
               t.p_value, t.q_value, t.pivot_hits, t.pivots, t.pivots_hit, t.precision, t.recall, t.alignment,
               t.cycles, t.net_pnl, t.n_buy, t.n_sell, t.buy_location, t.sell_location, t.buy_extreme,
               t.sell_extreme, t.null_extreme, t.maker_share
        {base} ORDER BY {SORTS[sort]}, w.address LIMIT :limit OFFSET :offset"""),
        {**params, "limit": page_size, "offset": (page - 1) * page_size}).mappings().all()
    return {
        "freshness": data_as_of(session),
        "scale": scale, "window": window, "group": group, "coin": coin, "sort": sort,
        "valid": window in VALID_WINDOWS[scale], "maker_heavy": MAKER_HEAVY, "extreme": EXTREME,
        "min_decisions": MIN_DECISIONS[scale], "page": page, "page_size": page_size, "total": total,
        "rows": [{**_clean(r), "flags": list(r["flags"] or []), "account_value": num(r["account_value"]),
                  "rank": (page - 1) * page_size + i + 1} for i, r in enumerate(rows)],
    }


@router.get("/wallets/{address}/timing")
def wallet_timing(address: str, session: Session = Depends(get_session), days: int = Query(120, ge=7, le=365)):
    address = parse_trader_key(address) or ''
    if not address:
        raise HTTPException(422, "invalid address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": address}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    scores = session.execute(text("""
        SELECT scale, time_window, coin_group, status, as_of, n_decisions, n_scored, location, null_mean, edge,
               edge_shrunk, p_value, q_value, pivot_hits, pivots, pivots_hit, precision, recall, alignment, cycles,
               net_pnl, n_buy, n_sell, buy_location, sell_location, buy_extreme, sell_extreme, null_extreme, maker_share,
               null_quantiles
        FROM timing_score WHERE wallet_id = :w AND version = :v"""), {"w": wid, "v": TIMING_VERSION}).mappings().all()
    # Calendar: per day and scale, notional-weighted mean location of scored decisions + pivot hits.
    calendar = session.execute(text("""
        SELECT date_trunc('day', t)::date AS day, scale, count(*) AS n,
               sum(location * weight) / nullif(sum(weight), 0) AS location,
               count(*) FILTER (WHERE pivot_ts IS NOT NULL) AS hits
        FROM decision_timing
        WHERE wallet_id = :w AND status = 'scored' AND t >= (SELECT max(t) FROM decision_timing WHERE wallet_id = :w)
              - make_interval(days => :days)
        GROUP BY 1, 2 ORDER BY 1"""), {"w": wid, "days": days}).mappings().all()
    statuses = dict(session.execute(text("""
        SELECT status, count(*) FROM decision_timing WHERE wallet_id = :w GROUP BY status"""), {"w": wid}).all())
    return {
        "version": TIMING_VERSION,
        "valid_windows": VALID_WINDOWS,
        "min_decisions": MIN_DECISIONS,
        "scores": [_clean(s) for s in scores],
        "calendar": [{**dict(c), "location": num(c["location"])} for c in calendar],
        "decision_status": statuses,
    }
