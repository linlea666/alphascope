import json
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import settings_store
from app.db import get_session
from app.research import binance_profiles as profiles
from app.research.profile_directory import ID

router = APIRouter(prefix="/api/v1")


class ProfileSettings(BaseModel):
    enabled: bool
    minutes: int = Field(ge=15, le=240)
    credential: SecretStr | None = None
    clear: bool = False
    # Older clients sent an explicit list of selected profiles.
    ids: list[str] | None = Field(default=None, max_length=10)


class Verify(BaseModel):
    credential: SecretStr | None = None


class Priority(BaseModel):
    pinned: bool


def lock(s):
    if not s.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": profiles.LOCK}):
        raise HTTPException(409, "busy")


def verify_and_store(s, credential):
    cfg = settings_store.get(s, profiles.KEY)
    now = datetime.now(UTC)
    if cfg.get("resume_at") and datetime.fromisoformat(cfg["resume_at"]) > now:
        raise HTTPException(429, "rate_limited")
    try:
        headers = profiles.credentials(credential.get_secret_value()) if credential else json.loads(
            settings_store.get_secret(s, profiles.KEY) or "null")
    except ValueError:
        raise HTTPException(422, "missing_fields") from None
    if not headers:
        raise HTTPException(422, "missing_fields")
    pids = s.scalars(text("""SELECT profile_id FROM binance_profile_directory
        ORDER BY priority_risk DESC, greatest(btc_seen,eth_seen) DESC NULLS LAST, pinned DESC,
        profile_id LIMIT 3""")).all()
    # An already-known public profile also allows onboarding before the first discovery sweep.
    if not pids:
        pids = ["5271501447655674625"]
    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            for pid in pids:
                shared, _ = profiles.fetch_profile(client, pid, headers, verify_only=True)
                if shared:
                    break
            else:
                raise profiles.Unavailable("no_shared_profile")
    except (profiles.Unavailable, httpx.HTTPError, ValueError, AttributeError, TypeError, KeyError) as exc:
        reason = exc.reason if isinstance(exc, profiles.Unavailable) else "upstream_error"
        values = {"verification":reason, "checked_at":now.isoformat()}
        if isinstance(exc, profiles.Unavailable) and exc.resume:
            values["resume_at"] = exc.resume.isoformat()
        if not credential and reason in ("access_denied", "source_restricted"):
            values["status"] = reason
        settings_store.put(s, profiles.KEY, values)
        s.commit()
        raise HTTPException(429 if reason == "rate_limited" else 422, reason) from None
    settings_store.put(s, profiles.KEY, {"status":"ready","verification":"ready","verified_at":now.isoformat(),
                       "checked_at":now.isoformat(),"resume_at":None},
                       secret=json.dumps(headers) if credential else None)


@router.get("/settings/binance-profiles")
def settings(s: Session = Depends(get_session)):
    return profiles.state(s)


@router.post("/settings/binance-profiles/verify")
def verify(body: Verify, s: Session = Depends(get_session)):
    lock(s)
    verify_and_store(s, body.credential)
    s.commit()
    return profiles.state(s)


@router.put("/settings/binance-profiles")
def configure(body: ProfileSettings, s: Session = Depends(get_session)):
    if body.ids and any(not ID.fullmatch(pid) for pid in body.ids):
        raise HTTPException(422, "invalid_id")
    lock(s)
    if body.enabled and (body.clear or not (body.credential or settings_store.has_secret(s, profiles.KEY))):
        raise HTTPException(422, "missing_fields")
    if body.credential and not body.clear:
        verify_and_store(s, body.credential)
    if body.ids is not None:
        for pid in body.ids:
            set_priority(pid, True, s)
    changes = {"minutes":body.minutes, "enabled":body.enabled}
    if body.clear:
        changes.update(status="idle", verification=None, verified_at=None, checked_at=None)
    settings_store.put(s, profiles.KEY, changes, clear_secret=body.clear)
    s.commit()
    return profiles.state(s)


def set_priority(pid, pinned, s):
    s.execute(text("""INSERT INTO binance_profile_directory(profile_id,pinned) VALUES(:pid,:pinned)
       ON CONFLICT(profile_id) DO UPDATE SET pinned=EXCLUDED.pinned"""), {"pid":pid,"pinned":pinned})


@router.put("/settings/binance-profiles/priority/{pid}")
def prioritize(pid: str, body: Priority, s: Session = Depends(get_session)):
    if not ID.fullmatch(pid):
        raise HTTPException(422, "invalid_id")
    set_priority(pid, body.pinned, s)
    s.commit()
    return {"pinned":body.pinned}


@router.get("/market/binance-profiles")
def market(coin: Literal["BTC", "ETH"], s: Session = Depends(get_session)):
    return profiles.structure(s, coin)
