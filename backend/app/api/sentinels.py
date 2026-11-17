"""Price-zone sentinels (saved zone queries that report new traders) and the daily digest."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app import settings_store
from app.alerts import digest
from app.api.alerts import _emails
from app.api.common import num
from app.db import get_session

router = APIRouter(prefix="/api/v1", tags=["sentinels"])
OWNER = "default"


class SentinelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    coin: str = Field(min_length=1, max_length=48)
    side: Literal["long", "short"]
    px_min: float = Field(ge=0)
    px_max: float = Field(gt=0)
    cohort: Literal["all", "timing", "watchlist"] = "all"
    venue: Literal["all", "hyperliquid", "okx"] = "all"
    min_notional: float = Field(0, ge=0)
    recipients: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("recipients")
    @classmethod
    def _recipients(cls, v: list[str]) -> list[str]:
        return _emails(v)


class SentinelPatch(BaseModel):
    enabled: bool


class DigestIn(BaseModel):
    enabled: bool
    recipients: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("recipients")
    @classmethod
    def _recipients(cls, v: list[str]) -> list[str]:
        return _emails(v)


@router.get("/alerts/sentinels")
def list_sentinels(session: Session = Depends(get_session)):
    rows = session.execute(text("""
        SELECT s.*, (SELECT count(*) FROM sentinel_hit h WHERE h.sentinel_id = s.id) AS hits
        FROM zone_sentinel s WHERE s.owner = :o ORDER BY s.created_at DESC"""), {"o": OWNER}).mappings().all()
    recent = session.execute(text("""
        SELECT h.sentinel_id, h.first_at, h.avg_px, h.notional, h.found_at, h.notify_status, w.address,
               w.display_name, w.venue
        FROM sentinel_hit h JOIN zone_sentinel s ON s.id = h.sentinel_id JOIN wallet w ON w.id = h.wallet_id
        WHERE s.owner = :o ORDER BY h.found_at DESC, h.notional DESC LIMIT 50"""), {"o": OWNER}).mappings().all()
    return {
        "sentinels": [{**dict(r), **{k: num(r[k]) for k in ("px_min", "px_max", "min_notional")}} for r in rows],
        "recent": [{**dict(r), "avg_px": num(r["avg_px"]), "notional": num(r["notional"])} for r in recent],
    }


@router.post("/alerts/sentinels")
def create_sentinel(body: SentinelIn, session: Session = Depends(get_session)):
    if body.px_min > body.px_max:
        raise HTTPException(422, "px_min must not exceed px_max")
    sid = session.execute(text("""
        INSERT INTO zone_sentinel (owner, name, coin, side, px_min, px_max, cohort, venue, min_notional, recipients)
        VALUES (:o, :name, :coin, :side, :lo, :hi, :cohort, :venue, :mn, :r) RETURNING id"""),
        {"o": OWNER, "name": body.name, "coin": body.coin.upper(), "side": body.side, "lo": body.px_min,
         "hi": body.px_max, "cohort": body.cohort, "venue": body.venue, "mn": body.min_notional,
         "r": body.recipients}).scalar_one()
    session.commit()
    return {"id": sid}


@router.patch("/alerts/sentinels/{sid}")
def update_sentinel(sid: int, body: SentinelPatch, session: Session = Depends(get_session)):
    n = session.execute(text("UPDATE zone_sentinel SET enabled = :e WHERE id = :id AND owner = :o"),
                        {"e": body.enabled, "id": sid, "o": OWNER}).rowcount
    if not n:
        raise HTTPException(404, "sentinel not found")
    session.commit()
    return {"ok": True}


@router.delete("/alerts/sentinels/{sid}")
def delete_sentinel(sid: int, session: Session = Depends(get_session)):
    n = session.execute(text("DELETE FROM zone_sentinel WHERE id = :id AND owner = :o"),
                        {"id": sid, "o": OWNER}).rowcount
    if not n:
        raise HTTPException(404, "sentinel not found")
    session.commit()
    return {"ok": True}


@router.get("/alerts/digest")
def latest_digest(session: Session = Depends(get_session)):
    row = session.execute(text("SELECT day, payload, created_at, emailed_at FROM daily_digest ORDER BY day DESC LIMIT 1")
                          ).mappings().first()
    cfg = settings_store.get(session, "digest")
    if row is None:
        return {"digest": None, "settings": cfg}
    payload = dict(row["payload"])
    payload.pop("significant_ids", None)
    return {"digest": {"day": str(row["day"]), "created_at": row["created_at"], "emailed_at": row["emailed_at"],
                       **payload}, "settings": cfg}


@router.post("/alerts/digest/run")
def run_digest(session: Session = Depends(get_session)):
    """Build today's digest now (without emailing); the scheduled run emails it."""
    sf = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    return digest.run(sf, datetime.now(UTC), email=False)


@router.put("/settings/digest")
def put_digest(body: DigestIn, session: Session = Depends(get_session)):
    settings_store.put(session, "digest", body.model_dump())
    session.commit()
    return settings_store.get(session, "digest")
