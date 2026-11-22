"""Coin-scoped replay, frozen positioning evidence and forward research archives."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy.dialects.postgresql import insert

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import (
    ResearchEvaluation,
    ResearchEvent,
    ResearchExperiment,
    ResearchForecast,
    ResearchFrame,
    ResearchNotice,
    ResearchReport,
    ResearchReview,
)
from app.research.forward import skill
from app.research.frames import VENUES, coverage, notice_rows
from app.research.replay import WINDOWS, catalog, details, event_info, summarize

router = APIRouter(prefix="/api/v1/research", tags=["market research"])
Mode = Literal["retrospective", "as_observed"]


def symbol(value):
    if value not in get_settings().research_coins:
        raise HTTPException(422, "unsupported research coin")
    return value


def asof(value):
    now = datetime.now(UTC)
    if value and value > now:
        raise HTTPException(422, "as_of must be in the past")
    return value or now


def get_event(s, eid, cutoff):
    e = s.get(ResearchEvent, eid)
    if e is None or e.available_at > cutoff:
        raise HTTPException(404, "event unavailable at this cutoff")
    return e


@router.get("/coins")
def coins():
    return {"coins": get_settings().research_coins, "windows": WINDOWS}


@router.get("/events")
def events(coin: str = "BTC", as_of: AwareDatetime | None = None, s: Session = Depends(get_session)):
    cutoff = asof(as_of)
    return {"coin": symbol(coin), "as_of": cutoff, "events": catalog(s, coin, cutoff)}


class Definition(BaseModel):
    coin: str
    title: str = Field(min_length=1, max_length=160)
    start_at: AwareDatetime
    end_at: AwareDatetime
    parent_id: int | None = None


@router.post("/events", status_code=201)
def define(body: Definition, s: Session = Depends(get_session)):
    coin, now = symbol(body.coin), datetime.now(UTC)
    if (
        body.end_at <= body.start_at
        or body.end_at > now
        or any(x.second or x.microsecond for x in (body.start_at, body.end_at))
    ):
        raise HTTPException(422, "expected a completed UTC minute-aligned window")
    parent = get_event(s, body.parent_id, now) if body.parent_id else None
    if parent and parent.coin != coin:
        raise HTTPException(422, "event revisions must keep their coin")
    bars = s.execute(
        text("""SELECT t,o FROM market_candle WHERE source='binance_um' AND coin=:coin
        AND interval='1m' AND t IN (:a,:b) AND fetched_at<=:now ORDER BY t"""),
        dict(coin=coin, a=body.start_at, b=body.end_at, now=now),
    ).all()
    if len(bars) != 2:
        raise HTTPException(422, "missing observed boundary price")
    key = parent.event_key if parent else str(uuid4())
    if parent:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
        revision = s.scalar(text("SELECT max(revision) FROM research_event WHERE event_key=:k"), {"k": key})
        if revision != parent.revision:
            raise HTTPException(409, "a newer event revision exists")
    e = ResearchEvent(
        event_key=key,
        revision=parent.revision + 1 if parent else 1,
        title=body.title,
        coin=coin,
        direction="up" if bars[1].o > bars[0].o else "down",
        cluster_key=parent.cluster_key if parent else key,
        source="binance_um",
        method="manual-minute-open-v1",
        start_at=body.start_at,
        end_at=body.end_at,
        start_px=bars[0].o,
        end_px=bars[1].o,
    )
    s.add(e)
    s.commit()
    return event_info(e)


@router.get("/events/{eid}")
def replay(
    eid: int,
    hours: int = 72,
    mode: Mode = "retrospective",
    as_of: AwareDatetime | None = None,
    synchronous_coin: str | None = None,
    s: Session = Depends(get_session),
):
    cutoff = asof(as_of)
    if hours not in WINDOWS:
        raise HTTPException(422, "unsupported research window")
    e = get_event(s, eid, cutoff)
    return details(s, e, symbol(synchronous_coin or e.coin), hours, cutoff, mode)


@router.get("/compare")
def compare(
    ids: list[int] = Query(min_length=2, max_length=5),
    hours: int = 72,
    mode: Mode = "retrospective",
    cross_coin: bool = False,
    as_of: AwareDatetime | None = None,
    s: Session = Depends(get_session),
):
    cutoff = asof(as_of)
    if len(set(ids)) != len(ids) or hours not in WINDOWS:
        raise HTTPException(422, "select distinct events and a supported window")
    selected = [get_event(s, i, cutoff) for i in ids]
    if len({e.coin for e in selected}) > 1 and not cross_coin:
        raise HTTPException(422, "cross_coin must be explicit")
    panels = [details(s, e, e.coin, hours, cutoff, mode) for e in selected]
    members = set.intersection(*({r["address"] for r in p["flow"]["contributors"]} for p in panels))
    for p in panels:
        fixed = summarize([r for r in p["flow"]["contributors"] if r["address"] in members])
        p["common_wallets"] = len(members)
        p["common_net"] = fixed["net"]
        f = p["flow"]
        gross = sum((f[k] or 0) for k in ("long_add", "long_cut", "short_add", "short_cut"))
        p["net_fraction"] = f["net"] / gross if gross else None
    spans = sorted((e.start_at - timedelta(hours=hours), e.end_at) for e in selected)
    ends = []
    for start, end in spans:
        if ends and start <= ends[-1]:
            ends[-1] = max(ends[-1], end)
        else:
            ends.append(end)
    return {
        "as_of": cutoff,
        "panels": panels,
        "overlap_clusters": len(ends),
        "common_basis": "retrospective_active_intersection",
    }


def summary(s, coin, cutoff):
    latest = []
    for venue in VENUES:
        r = (
            s.execute(
                select(ResearchFrame.id, ResearchFrame.metrics, ResearchFrame.context, ResearchFrame.available_at)
                .where(
                    ResearchFrame.coin == coin, ResearchFrame.venue == venue, ResearchFrame.available_at <= cutoff
                )
                .order_by(ResearchFrame.available_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if r:
            latest.append({"venue": venue, **dict(r), "stale": cutoff - r["available_at"] > timedelta(hours=2)})
    return {
        "coin": coin,
        "as_of": cutoff,
        "frames": latest,
        "coverage": coverage(s, coin, cutoff),
        "notices": notice_rows(s, coin, cutoff, cutoff - timedelta(hours=48)),
    }


@router.get("/summary")
def market_summary(coin: str = "BTC", as_of: AwareDatetime | None = None, s: Session = Depends(get_session)):
    return summary(s, symbol(coin), asof(as_of))


class Watch(BaseModel):
    watching: bool


@router.put("/notices/{nid}/watch")
def watch(nid: int, body: Watch, s: Session = Depends(get_session)):
    n = s.get(ResearchNotice, nid)
    if not n:
        raise HTTPException(404, "notice not found")
    n.watching = body.watching
    s.commit()
    return {"id": nid, "watching": n.watching}


@router.get("/frames")
def timeline(
    coin: str = "BTC",
    venue: Literal["hyperliquid", "okx"] = "hyperliquid",
    days: int = Query(7, ge=1, le=90),
    as_of: AwareDatetime | None = None,
    s: Session = Depends(get_session),
):
    end = asof(as_of)
    rows = s.execute(
        select(
            ResearchFrame.id,
            ResearchFrame.available_at,
            ResearchFrame.observed_at,
            ResearchFrame.metrics,
            ResearchFrame.context,
        )
        .where(
            ResearchFrame.coin == symbol(coin),
            ResearchFrame.venue == venue,
            ResearchFrame.available_at <= end,
            ResearchFrame.available_at >= end - timedelta(days=days),
        )
        .order_by(ResearchFrame.available_at)
    ).mappings()
    return {"coin": coin, "venue": venue, "rows": [dict(r) for r in rows]}


@router.get("/frames/{fid}")
def frame_detail(
    fid: int,
    coin: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    s: Session = Depends(get_session),
):
    frame = s.get(ResearchFrame, fid)
    if not frame or frame.coin != symbol(coin):
        raise HTTPException(404, "frame not found for this coin")
    accounts = sorted(frame.accounts, key=lambda a: (-a["notional_usd"], a["address"]))
    return {
        **{
            k: getattr(frame, k)
            for k in (
                "id",
                "coin",
                "venue",
                "bucket_ts",
                "observed_at",
                "available_at",
                "context",
                "metrics",
                "version",
            )
        },
        "accounts": accounts[offset : offset + limit],
        "total_accounts": len(accounts),
    }


@router.get("/archive")
def archive(coin: str = "BTC", s: Session = Depends(get_session)):
    studies = s.scalars(
        select(ResearchExperiment).where(ResearchExperiment.coin == symbol(coin)).order_by(ResearchExperiment.id)
    ).all()
    forecasts = s.scalars(
        select(ResearchForecast)
        .where(ResearchForecast.coin == coin)
        .order_by(ResearchForecast.issued_at.desc(), ResearchForecast.horizon)
        .limit(120)
    ).all()
    graded = {}
    for r in s.execute(
        text("""SELECT model,horizon,outcome->>'status' status,(outcome->>'p_brier')::float brier,
        (outcome->>'baseline_brier')::float base_brier,(outcome->>'p_log_loss')::float log_loss,
        (outcome->>'baseline_log_loss')::float base_log_loss FROM research_forecast
        WHERE coin=:coin ORDER BY model,horizon,issued_at"""),
        {"coin": coin},
    ).mappings():
        graded.setdefault((r["model"], r["horizon"]), []).append(r)
    scores = []
    for (model, horizon), rows in graded.items():
        done = [r for r in rows if r["status"] == "settled"]
        missing = sum(r["status"] == "missing" for r in rows)
        mean = (lambda k: sum(r[k] for r in done) / len(done)) if done else (lambda k: None)
        scores.append({
            "model": model, "horizon": horizon, "n": len(done), "missing": missing,
            "missing_rate": missing / (len(done) + missing) if done or missing else None,
            "pending": len(rows) - len(done) - missing,
            "brier": mean("brier"), "base_brier": mean("base_brier"),
            "log_loss": mean("log_loss"), "base_log_loss": mean("base_log_loss"),
            "skill": skill([(r["brier"], r["base_brier"]) for r in done], horizon),
        })
    calibration = (
        s.execute(
            text("""
        WITH predictions AS (
            SELECT model,horizon,cls,probability::float p,(outcome->>'class'=cls)::int hit
            FROM research_forecast
            CROSS JOIN LATERAL jsonb_array_elements_text(evidence->'p') WITH ORDINALITY AS x(probability,i)
            CROSS JOIN LATERAL (SELECT (ARRAY['down','flat','up'])[i::int] cls) c
            WHERE coin=:coin AND outcome->>'status'='settled'
        )
        SELECT model,horizon,cls,least(9,floor(p*10)::int) bin,count(*) n,avg(p) predicted,avg(hit)::float observed
        FROM predictions GROUP BY 1,2,3,4 ORDER BY 1,2,3,4
    """),
            {"coin": coin},
        )
        .mappings()
        .all()
    )
    return {
        "coin": coin,
        "studies": [
            {
                "id": st.id,
                "version": st.version,
                "review": s.scalar(
                    select(ResearchReview)
                    .where(ResearchReview.experiment_id == st.id)
                    .order_by(ResearchReview.id.desc())
                    .limit(1)
                ),
                "hypothesis": st.hypothesis,
                "specification": st.specification,
                "registered_at": st.registered_at,
                "runs": list(
                    s.scalars(
                        select(ResearchEvaluation)
                        .where(ResearchEvaluation.experiment_id == st.id)
                        .order_by(ResearchEvaluation.cutoff.desc())
                        .limit(30)
                    )
                ),
            }
            for st in studies
        ],
        "forecasts": forecasts,
        "calibration": [dict(r) for r in calibration],
        "scores": scores,
        "status": "shadow",
    }


@router.get("/events/{eid}/similar")
def nearest(
    eid: int,
    hours: int = 72,
    cross_coin: bool = False,
    mode: Mode = "retrospective",
    as_of: AwareDatetime | None = None,
    s: Session = Depends(get_session),
):
    from app.research.evidence import similar

    if hours not in WINDOWS:
        raise HTTPException(422, "unsupported research window")
    cutoff = asof(as_of)
    return similar(s, get_event(s, eid, cutoff), cutoff, hours, cross_coin, mode)


class ReportRequest(BaseModel):
    event_id: int
    coin: str
    hours: int = 72
    mode: Mode = "retrospective"
    vintage: AwareDatetime


@router.post("/reports", status_code=201)
def save_report(body: ReportRequest, s: Session = Depends(get_session)):
    s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    key = {
        "event_id": body.event_id,
        "coin": symbol(body.coin),
        "hours": body.hours,
        "mode": body.mode,
        "vintage": asof(body.vintage),
        "source_version": "event-flow-v1",
    }
    if body.hours not in WINDOWS:
        raise HTTPException(422, "unsupported research window")
    e = get_event(s, body.event_id, key["vintage"])
    existing = s.scalar(select(ResearchReport.id).filter_by(**key))
    if existing is not None:
        return {"id": existing}
    payload = jsonable_encoder(details(s, e, body.coin, body.hours, body.vintage, body.mode))
    # Publication is immutable. Concurrent saves of an identical vintage share one key.
    stmt = (
        insert(ResearchReport).values(**key, payload=payload).on_conflict_do_nothing().returning(ResearchReport.id)
    )
    from sqlalchemy.exc import OperationalError

    try:
        rid = s.scalar(stmt)
        s.commit()
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) != "40001":
            raise
        s.rollback()
        rid = None
    if rid is None:
        rid = s.scalar(select(ResearchReport.id).filter_by(**key))
        if rid is None:
            raise HTTPException(409, "retry report publication")
    return {"id": rid}


@router.get("/reports/{rid}")
def report(rid: int, coin: str, s: Session = Depends(get_session)):
    r = s.get(ResearchReport, rid)
    if r is None or r.coin != symbol(coin):
        raise HTTPException(404, "report unavailable for this coin")
    return {"id": rid, "published_at": r.published_at, "source_version": r.source_version, "detail": r.payload}


class FactorReview(BaseModel):
    evaluation_id: int
    decision: Literal["approve", "reject", "withdraw"]
    rationale: str = Field(min_length=1, max_length=2000)


@router.post("/experiments/{sid}/reviews", status_code=201)
def review_factor(sid: int, body: FactorReview, s: Session = Depends(get_session)):
    from app.research.forward import STUDY_VERSION

    study = s.scalar(select(ResearchExperiment).where(ResearchExperiment.id == sid).with_for_update())
    latest = s.scalar(
        select(ResearchEvaluation)
        .where(ResearchEvaluation.experiment_id == sid)
        .order_by(ResearchEvaluation.cutoff.desc())
        .limit(1)
    )
    if not study or not latest:
        raise HTTPException(404, "experiment has no published evaluation")
    if latest.id != body.evaluation_id:
        raise HTTPException(409, "review requires the latest evaluation")
    if body.decision == "approve" and (study.version != STUDY_VERSION or not latest.result.get("review_eligible")):
        raise HTTPException(422, "insufficient prospective validation")
    reason = body.rationale.strip()
    if not reason:
        raise HTTPException(422, "review rationale is required")
    prior = s.scalar(
        select(ResearchReview)
        .where(ResearchReview.experiment_id == sid)
        .order_by(ResearchReview.id.desc())
        .limit(1)
    )
    if prior and (prior.evaluation_id, prior.decision, prior.rationale) == (latest.id, body.decision, reason):
        return {"id": prior.id}
    item = ResearchReview(experiment_id=sid, evaluation_id=latest.id, decision=body.decision, rationale=reason)
    s.add(item)
    s.commit()
    return {"id": item.id}


@router.get("/events/{eid}/sources")
def sources(
    eid: int,
    coin: str,
    venue: Literal["hyperliquid", "okx"],
    hours: int = 72,
    mode: Mode = "retrospective",
    as_of: AwareDatetime | None = None,
    wallet: str | None = Query(None, max_length=128),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    s: Session = Depends(get_session),
):
    cutoff = asof(as_of)
    event = get_event(s, eid, cutoff)
    if hours not in WINDOWS:
        raise HTTPException(422, "unsupported window")
    if mode == "as_observed":
        cutoff = min(cutoff, event.start_at)
    args = dict(
        c=symbol(coin),
        a=event.start_at - timedelta(hours=hours),
        b=event.start_at,
        t=cutoff,
        w=wallet,
        offset=offset,
        n=limit + 1,
    )
    if venue == "hyperliquid":
        query = """SELECT w.address, f.tid::text reference,f.time event_time,r.fetched_at known_at,
                 f.sz quantity,f.px price,f.side,f.start_position,r.id archive_id,r.sha256
                 FROM fill f JOIN wallet w ON w.id=f.wallet_id JOIN raw_object r ON r.id=f.raw_object_id
                 WHERE f.coin=:c AND f.time>=:a AND f.time<:b AND r.fetched_at<=:t
                 AND (CAST(:w AS text) IS NULL OR w.address=:w) ORDER BY f.time DESC,f.wallet_id,f.tid"""
    else:
        query = """SELECT w.address,p.pos_id reference,p.open_time event_time,p.fetched_at known_at,p.max_size quantity,
                 p.open_avg_px price,p.direction side,p.close_type,p.liquidated FROM okx_position p
                 JOIN wallet w ON w.id=p.wallet_id WHERE p.coin=:c AND p.open_time>=:a AND p.open_time<:b
                 AND p.fetched_at<=:t AND (CAST(:w AS text) IS NULL OR w.address=:w)
                 ORDER BY p.open_time DESC,p.wallet_id,p.pos_id"""
    rows = s.execute(text(query + " OFFSET :offset LIMIT :n"), args).mappings().all()
    return {
        "rows": [dict(r) for r in rows[:limit]],
        "has_more": len(rows) > limit,
        "cutoff": cutoff,
        "event": event_info(event),
        "coin": coin,
        "venue": venue,
        "hours": hours,
        "mode": mode,
    }


@router.get("/factors")
def factor_screen(coin: str, s: Session = Depends(get_session)):
    """The latest nightly historical factor screen for one coin (exploratory; see app.research.factor_lab)."""
    coin = symbol(coin)
    row = s.execute(text("SELECT computed_at, result FROM factor_lab_run ORDER BY id DESC LIMIT 1")).first()
    if row is None:
        return {"coin": coin, "status": "not_run", "computed_at": None, "factors": {}}
    r = row.result
    return {"coin": coin, "computed_at": row.computed_at, "version": r["version"], "fdr": r["fdr"],
            "oos_alpha": r["oos_alpha"], "test_count": r["tests"], "factors": r["factors"],
            **r["coins"].get(coin, {"status": "not_run"})}


def _tier(concluded, testing):
    return "verified" if concluded else "watching" if testing else "descriptive"


@router.get("/conclusions")
def conclusions(s: Session = Depends(get_session)):
    """What the research currently supports, one item per question, each with its evidence tier: verified (a pre-set
    test reached its decision, whichever way), watching (still accumulating, or a screening candidate) or descriptive
    (a reading, not a claim)."""
    from app.factors import copy_validation
    from app.research.forward import MODEL, STUDY_VERSION

    items = []
    lab = s.execute(text("SELECT computed_at, result FROM factor_lab_run ORDER BY id DESC LIMIT 1")).first()
    for coin in get_settings().research_coins:
        rows = s.execute(text("""SELECT (outcome->>'p_brier')::float m, (outcome->>'baseline_brier')::float b
            FROM research_forecast WHERE coin=:c AND model=:m AND horizon=24 AND outcome->>'status'='settled'
            ORDER BY issued_at"""), {"c": coin, "m": MODEL}).all()
        k = skill([(m, b) for m, b in rows], 24)
        items.append({"kind": "forecast", "coin": coin, "tier": _tier(k["p"] is not None, True), "data": k})
        for st in s.scalars(select(ResearchExperiment).where(ResearchExperiment.coin == coin,
                                                             ResearchExperiment.version == STUDY_VERSION)):
            ev = s.scalar(select(ResearchEvaluation).where(ResearchEvaluation.experiment_id == st.id)
                          .order_by(ResearchEvaluation.cutoff.desc()).limit(1))
            review = s.scalar(select(ResearchReview).where(ResearchReview.experiment_id == st.id)
                              .order_by(ResearchReview.id.desc()).limit(1))
            r = ev.result if ev else {}
            decided = review is not None and review.decision in ("approve", "reject")
            items.append({"kind": "study", "coin": coin, "tier": _tier(decided, True), "data": {
                "hypothesis": st.hypothesis, "status": r.get("status", "insufficient"), "pairs": r.get("pairs", 0),
                "tested_pairs": r.get("tested_pairs", 0), "weeks": r.get("weeks", 0), "effect": r.get("effect"),
                "q": r.get("q"), "eligible": r.get("review_eligible", False),
                "review": review.decision if review else None}})
        entry = (lab.result["coins"].get(coin) if lab else None) or {}
        if entry.get("status") == "screened":
            regime = (entry.get("today") or {}).get("regime")
            items.append({"kind": "regime", "coin": coin, "tier": "descriptive", "data": {
                "regime": regime, "day": entry["today"]["day"], "stats": entry["regimes"].get(regime),
                "all": entry["regimes"]["all"]}})
            found = [t for t in entry["tests"] if t["status"] == "candidate"]
            items.append({"kind": "factors", "coin": coin, "tier": _tier(False, bool(found)), "data": {
                "candidates": found, "tests": lab.result["tests"], "computed_at": lab.computed_at}})
        obs = s.execute(text("""SELECT payload->'readings'->'funding'->'data' FROM exchange_observation
            WHERE coin=:c ORDER BY bucket DESC LIMIT 1"""), {"c": coin}).scalar()
        if obs:
            items.append({"kind": "funding", "coin": coin, "tier": "descriptive", "data": {
                "rate": obs.get("rate"), "level": (obs.get("history") or {}).get("level"),
                "percentile": (obs.get("history") or {}).get("percentile"), "source_at": obs.get("source_at")}})
    score = copy_validation.evaluate(s, copy_validation.HORIZONS[0])
    items.append({"kind": "copy_score", "coin": None, "tier": _tier(score["status"] == "tested", True), "data": {
        k: score[k] for k in ("horizon", "status", "graded_days", "need_days", "mean_rho", "p_value")}
        | {"tested_days": len(score["days"])}})
    persistence = s.scalar(text("SELECT result FROM copy_persistence_run ORDER BY id DESC LIMIT 1"))
    for run in (persistence or {}).get("runs", []):
        items.append({"kind": "persistence", "coin": None, "tier": _tier(run["status"] == "tested", True), "data": {
            k: run.get(k) for k in ("venue", "horizon", "lookback", "status", "mean_rho", "p_value",
                                    "top_minus_bottom", "need_days", "shared_denominator", "sign_mean", "sign_p")}
            | {"tested_days": sum(d["rho"] is not None for d in run["dates"])}})
    return {"items": items, "at": datetime.now(UTC)}
