import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import settings_store
from app.db import get_session
from app.research import binance_profiles as profiles

router = APIRouter(prefix="/api/v1")


class ProfileSettings(BaseModel):
    enabled: bool
    ids: list[str] = Field(max_length=10)
    minutes: int = Field(ge=15, le=240)
    credential: SecretStr | None = None
    clear: bool = False


@router.get("/settings/binance-profiles")
def settings(s: Session = Depends(get_session)):
    return profiles.state(s)


@router.put("/settings/binance-profiles")
def configure(body: ProfileSettings, s: Session = Depends(get_session)):
    if any(not re.fullmatch(r"[0-9]{19}", pid) for pid in body.ids):
        raise HTTPException(422, "Use 19-digit Smart Money profile IDs")
    secret = None
    if body.credential:
        try:
            secret = json.dumps(profiles.credentials(body.credential.get_secret_value()))
        except ValueError:
            raise HTTPException(422, "Missing credential fields") from None
    if not s.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": profiles.LOCK}):
        raise HTTPException(409, "Collection in progress")
    cfg = profiles.state(s)
    if body.enabled and (not body.ids or body.clear or not (secret or cfg["has_credential"])):
        raise HTTPException(422, "Select a profile and provide credentials")
    changes = {"ids": list(dict.fromkeys(body.ids)), "minutes": body.minutes, "enabled": body.enabled}
    if secret or body.clear:
        changes["status"] = "pending" if secret and not body.clear else "idle"
        changes["attempts"] = {}
    settings_store.put(s, profiles.KEY, changes, secret=secret, clear_secret=body.clear)
    s.commit()
    return profiles.state(s)


@router.get("/market/binance-profiles")
def market(coin: Literal["BTC", "ETH"], s: Session = Depends(get_session)):
    return profiles.structure(s, coin)
