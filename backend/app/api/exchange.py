from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.research import Watch, asof, symbol
from app.db import get_session
from app.research.exchange import details, latest, snapshot

router = APIRouter(prefix="/api/v1/research/exchange", tags=["exchange research"])


@router.get("/summary")
def summary(coin: str, as_of: AwareDatetime | None = None, s: Session = Depends(get_session)):
    cutoff = asof(as_of)
    return snapshot(latest(s, symbol(coin), cutoff), cutoff)


@router.get("/details")
def detail_page(
    coin: str,
    window: Literal["ALL", "30m", "1h", "24h", "7D"] = "ALL",
    side: Literal["all", "long", "short"] = "all",
    cohort: Literal["traders", "whales"] = "traders",
    order: Literal["ASC", "DESC"] = "DESC",
    page: int = Query(1, ge=1, le=10000),
):
    try:
        return details(symbol(coin), window, side, cohort, order, page)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(503, "Binance detail page unavailable") from exc


@router.get("/evidence")
def evidence(
    coin: str,
    observation: int | None = None,
    as_of: AwareDatetime | None = None,
    s: Session = Depends(get_session),
):
    coin = symbol(coin)
    cutoff = asof(as_of)
    if observation is None:
        row = latest(s, coin, cutoff)
    else:
        row = (
            s.execute(
                text("""SELECT * FROM exchange_observation WHERE coin=:c AND id=:id AND published_at<=:t"""),
                {"c": coin, "id": observation, "t": cutoff},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "observation unavailable for this coin")
    if not row:
        return {"observation": None, "groups": [], "history": [], "saved": [], "coverage_start": None}
    vintage = row["published_at"]
    params = {"c": coin, "t": vintage, "start": vintage - timedelta(days=1)}
    groups = (
        s.execute(
            text("""SELECT DISTINCT ON (venue) id,venue,available_at,
          metrics->'smart' AS specialist, metrics->'control' AS control FROM research_frame
          WHERE coin=:c AND available_at<=:t ORDER BY venue,available_at DESC"""),
            params,
        )
        .mappings()
        .all()
    )
    history = (
        s.execute(
            text("""SELECT id,bucket,published_at,payload->'readings'->'ALL'->>'at' AS observed,
          CASE WHEN NOT (payload->'errors' ? 'ALL') THEN (payload#>>'{readings,ALL,data,traders,money_share}')::float END AS share,
          CASE WHEN NOT (payload->'errors' ? 'ALL') THEN (payload#>>'{readings,ALL,data,traders,head_share}')::float END AS smart_accounts,
          CASE WHEN NOT (payload->'errors' ? 'binance_accounts') AND published_at -
            (payload#>>'{readings,binance_accounts,data,source_at}')::timestamptz <= interval '10 minutes'
            THEN (payload#>>'{readings,binance_accounts,data,long_share}')::float END AS overall_accounts,
          CASE WHEN NOT (payload->'errors' ? '24h') THEN (payload#>>'{readings,24h,data,traders,net}')::float END AS flow,
          payload#>>'{readings,24h,at}' AS flow_at FROM exchange_observation
          WHERE coin=:c AND published_at BETWEEN :start AND :t ORDER BY bucket"""),
            params,
        )
        .mappings()
        .all()
    )
    saved = (
        s.execute(
            text("""SELECT id,published_at FROM exchange_observation WHERE coin=:c AND watching
          AND published_at<=:t ORDER BY published_at DESC LIMIT 30"""),
            {"c": coin, "t": cutoff},
        )
        .mappings()
        .all()
    )
    first = s.scalar(
        text("SELECT min(published_at) FROM exchange_observation WHERE coin=:c AND published_at<=:t"), params
    )
    return {
        "observation": snapshot(row, vintage if observation else cutoff),
        "groups": groups,
        "history": history,
        "saved": saved,
        "coverage_start": first,
    }


@router.put("/{oid}/watch")
def watch(oid: int, body: Watch, s: Session = Depends(get_session)):
    found = s.scalar(
        text("UPDATE exchange_observation SET watching=:w WHERE id=:id RETURNING id"),
        {"id": oid, "w": body.watching},
    )
    if not found:
        raise HTTPException(404, "unknown observation")
    s.commit()
    return {"id": found, "watching": body.watching}
