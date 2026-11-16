from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import data_as_of, num
from app.config import get_settings
from app.db import get_session
from app.research.swings import ANCHOR_TOLERANCE, LEVELS, MAX_LUCK_P, MIN_CANDIDATE_AV, MIN_CYCLE_RETURN, SWING_VERSION

router = APIRouter(prefix="/api/v1/swings", tags=["swings"])

MAJOR_COINS = ("BTC", "ETH")


def _legs(session: Session, coin: str, level: str) -> list[dict]:
    rows = session.execute(text("""
        SELECT id, direction, start_ts, start_px, end_ts, end_px, change, confirmed FROM swing_leg
        WHERE coin = :coin AND version = :v AND level = :lvl ORDER BY start_ts"""),
        {"coin": coin, "v": SWING_VERSION, "lvl": level}).mappings().all()
    return [{**dict(r), "start_px": num(r["start_px"]), "end_px": num(r["end_px"]), "change": num(r["change"])}
            for r in rows]


@router.get("")
def swings_overview(session: Session = Depends(get_session), since: str = "2026-01-01"):
    coin = get_settings().swing_coin
    major = _legs(session, coin, "major")
    candles = session.execute(text("""
        SELECT t, o, h, l, c FROM market_candle
        WHERE source = 'hyperliquid' AND coin = :coin AND interval = '1d' AND t >= CAST(:since AS date)
        ORDER BY t"""), {"coin": coin, "since": since}).all()
    leg_stats = {r.leg_id: r for r in session.execute(text("""
        SELECT leg_id, count(*) AS covered, avg((twr > 0)::int) AS profitable_share,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY twr) AS median_twr
        FROM wallet_leg_return GROUP BY leg_id""")).all()}
    profile = session.execute(text("""
        SELECT count(*) AS screened, count(*) FILTER (WHERE candidate) AS candidates,
               count(*) FILTER (WHERE current_cycle_both) AS cycle_both, max(computed_at) AS computed_at
        FROM wallet_swing_profile WHERE version = :v"""), {"v": SWING_VERSION}).mappings().one()
    fills = session.execute(text("""
        SELECT count(*) AS queued, count(*) FILTER (WHERE last_synced_at IS NOT NULL) AS synced,
               count(*) FILTER (WHERE truncated) AS truncated, coalesce(sum(n_fills), 0) AS fills
        FROM fill_sync""")).mappings().one()
    return {
        "freshness": data_as_of(session),
        "coin": coin,
        "version": SWING_VERSION,
        "thresholds": LEVELS,
        "anchor_tolerance_days": ANCHOR_TOLERANCE.days,
        "criteria": {"min_account_value": MIN_CANDIDATE_AV, "min_cycle_return": MIN_CYCLE_RETURN,
                     "max_luck_p": MAX_LUCK_P},
        "legs": [{**leg, "covered": leg_stats[leg["id"]].covered if leg["id"] in leg_stats else 0,
                  "profitable_share": num(leg_stats[leg["id"]].profitable_share) if leg["id"] in leg_stats else None,
                  "median_twr": num(leg_stats[leg["id"]].median_twr) if leg["id"] in leg_stats else None}
                 for leg in major],
        "minor_legs": [leg for leg in _legs(session, coin, "minor") if str(leg["start_ts"]) >= since],
        "candles": [[c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in candles],
        "screen": dict(profile),
        "fill_archive": dict(fills),
    }


@router.get("/candidates")
def swing_candidates(
    session: Session = Depends(get_session),
    scope: Literal["candidates", "cycle", "all"] = "candidates",
    sort: Literal["evidence", "cycle", "luck", "btc_share"] = "evidence",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    coin = get_settings().swing_coin
    legs = _legs(session, coin, "major")
    recent = [leg for leg in legs if leg["start_ts"].year >= 2026]
    cycle = recent[-2:]
    where = {"candidates": "p.candidate", "cycle": "p.current_cycle_both", "all": "true"}[scope]
    order = {
        "evidence": "p.current_cycle_both DESC, p.luck_p ASC NULLS LAST, p.min_cycle_return DESC NULLS LAST",
        "cycle": "p.min_cycle_return DESC NULLS LAST",
        "luck": "p.luck_p ASC NULLS LAST, p.legs_profitable DESC",
        "btc_share": "a.major_share DESC NULLS LAST, p.min_cycle_return DESC NULLS LAST",
    }[sort]
    cycle_start = cycle[0]["start_ts"] if cycle else None
    base = f"""
        FROM wallet_swing_profile p JOIN wallet w ON w.id = p.wallet_id
        LEFT JOIN fill_sync fs ON fs.wallet_id = p.wallet_id
        LEFT JOIN LATERAL (
          SELECT sum(closed_pnl) FILTER (WHERE coin = ANY(:majors)) AS major_pnl,
                 sum(closed_pnl) AS total_pnl,
                 sum(closed_pnl) FILTER (WHERE coin = ANY(:majors))
                   / nullif(sum(abs(closed_pnl)), 0) AS major_share
          FROM fill f WHERE f.wallet_id = p.wallet_id AND f.time >= :cycle_start
        ) a ON true
        WHERE p.version = :v AND {where}"""
    params = {"v": SWING_VERSION, "majors": list(MAJOR_COINS), "cycle_start": cycle_start}
    total = session.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = session.execute(text(f"""
        SELECT w.id, w.address, w.display_name, w.account_value, w.tier, w.quality_flags || w.enrichment_flags AS flags,
               p.legs_covered, p.legs_profitable, p.up_profitable, p.down_profitable, p.up_capture, p.down_capture,
               p.luck_p, p.current_cycle_both, p.min_cycle_return, p.candidate,
               fs.status AS fill_status, fs.n_fills, fs.earliest_fill_at, fs.truncated, fs.last_synced_at,
               a.major_pnl, a.total_pnl, a.major_share
        {base}
        ORDER BY {order}, w.address
        LIMIT :limit OFFSET :offset"""), {**params, "limit": page_size, "offset": (page - 1) * page_size}).mappings().all()
    ids = [r["id"] for r in rows]
    leg_returns: dict[int, dict[int, dict]] = {}
    if ids:
        for lr in session.execute(text("""
            SELECT wallet_id, leg_id, twr, pnl, bench_return FROM wallet_leg_return WHERE wallet_id = ANY(:ids)"""),
                {"ids": ids}).all():
            leg_returns.setdefault(lr.wallet_id, {})[lr.leg_id] = {
                "twr": num(lr.twr), "pnl": num(lr.pnl), "bench": num(lr.bench_return)}
    return {
        "freshness": data_as_of(session),
        "scope": scope,
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "total": total,
        "legs": recent,
        "cycle_leg_ids": [leg["id"] for leg in cycle],
        "major_coins": MAJOR_COINS,
        "rows": [
            {
                **{k: r[k] for k in ("address", "display_name", "tier", "legs_covered", "legs_profitable",
                                     "up_profitable", "down_profitable", "current_cycle_both", "candidate",
                                     "fill_status", "n_fills", "earliest_fill_at", "truncated", "last_synced_at")},
                "flags": list(r["flags"] or []),
                "account_value": num(r["account_value"]),
                "up_capture": num(r["up_capture"]),
                "down_capture": num(r["down_capture"]),
                "luck_p": num(r["luck_p"]),
                "min_cycle_return": num(r["min_cycle_return"]),
                "realized": {"major": num(r["major_pnl"]), "total": num(r["total_pnl"]),
                             "major_share": num(r["major_share"])},
                "legs": leg_returns.get(r["id"], {}),
            }
            for r in rows
        ],
    }
