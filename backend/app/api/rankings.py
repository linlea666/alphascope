from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import data_as_of, num
from app.db import get_session
from app.quant.quality import ALL_FLAGS, DEFAULT_RANKING_EXCLUSIONS

router = APIRouter(prefix="/api/v1", tags=["rankings"])

Window = Literal["day", "week", "month", "alltime"]
SORTS = {
    "pnl": "w.{w}_pnl",
    "roi": "w.{w}_roi",
    "vlm": "w.{w}_vlm",
    "account_value": "w.account_value",
    "twr_30d": "m.twr",
    "mdd_30d": "m.mdd",
}


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


@router.get("/rankings")
def rankings(
    session: Session = Depends(get_session),
    window: Window = "month",
    sort: str = "pnl",
    order: Literal["desc", "asc"] = "desc",
    min_av: float | None = Query(None, ge=0),
    max_av: float | None = Query(None, ge=0),
    min_vlm: float | None = Query(None, ge=0),
    tier: str | None = Query(None, description="comma separated tiers, e.g. B,C"),
    exclude_flags: str | None = Query(None, description="comma separated; defaults to the noise filter"),
    raw: bool = Query(False, description="disable the default noise filter and activity requirement"),
    enriched_only: bool = False,
    q: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if sort not in SORTS:
        raise HTTPException(422, f"sort must be one of {sorted(SORTS)}")
    excluded = _csv(exclude_flags) if exclude_flags is not None else ([] if raw else list(DEFAULT_RANKING_EXCLUSIONS))
    unknown = set(excluded) - set(ALL_FLAGS)
    if unknown:
        raise HTTPException(422, f"unknown flags {sorted(unknown)}")
    tiers = _csv(tier)

    where = ["w.present_in_latest"]
    params: dict = {}
    if not raw:
        where.append(f"w.{window}_vlm > 0")
    if excluded:
        where.append("NOT (w.quality_flags && CAST(:ex AS text[]) OR w.enrichment_flags && CAST(:ex AS text[]))")
        params["ex"] = excluded
    if min_av is not None:
        where.append("w.account_value >= :min_av")
        params["min_av"] = min_av
    if max_av is not None:
        where.append("w.account_value <= :max_av")
        params["max_av"] = max_av
    if min_vlm is not None:
        where.append(f"w.{window}_vlm >= :min_vlm")
        params["min_vlm"] = min_vlm
    if tiers:
        where.append("w.tier = ANY(CAST(:tiers AS text[]))")
        params["tiers"] = tiers
    if enriched_only or sort in ("twr_30d", "mdd_30d"):
        where.append("m.twr IS NOT NULL")
    if q:
        where.append("(w.address LIKE :qaddr OR w.display_name ILIKE :qname)")
        params["qaddr"] = q.lower().strip() + "%"
        params["qname"] = "%" + q.strip() + "%"

    sort_sql = SORTS[sort].format(w=window)
    base = f"""
        FROM wallet w
        LEFT JOIN wallet_metric m ON m.wallet_id = w.id AND m.metric_window = '30d'
        WHERE {" AND ".join(where)}"""
    total = session.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = session.execute(text(f"""
        SELECT w.address, w.display_name, w.account_value, w.{window}_pnl AS pnl, w.{window}_roi AS roi,
               w.{window}_vlm AS vlm, w.tier, w.quality_flags, w.enrichment_flags, w.first_seen_at,
               m.twr, m.mdd, m.reliability, m.notes
        {base}
        ORDER BY {sort_sql} {order.upper()} NULLS LAST, w.address
        LIMIT :limit OFFSET :offset"""), {**params, "limit": page_size, "offset": (page - 1) * page_size}).all()

    return {
        "window": window,
        "sort": sort,
        "order": order,
        "page": page,
        "page_size": page_size,
        "total": total,
        "excluded_flags": excluded,
        "activity_required": not raw,
        "freshness": data_as_of(session),
        "rows": [
            {
                "rank": (page - 1) * page_size + i + 1,
                "address": r.address,
                "display_name": r.display_name,
                "account_value": num(r.account_value),
                "pnl": num(r.pnl),
                "roi": num(r.roi),
                "vlm": num(r.vlm),
                "tier": r.tier,
                "flags": list(r.quality_flags or []) + list(r.enrichment_flags or []),
                "first_seen_at": r.first_seen_at,
                "twr_30d": num(r.twr),
                "mdd_30d": num(r.mdd),
                "reliability_30d": r.reliability,
            }
            for i, r in enumerate(rows)
        ],
    }
