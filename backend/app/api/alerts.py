"""Alert subscriptions, recent position events, the email log and owner settings (SMTP, live monitor)."""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import settings_store
from app.alerts.diff import EVENT_KINDS
from app.alerts.mailer import send
from app.api.common import num
from app.db import get_session
from app.traders import parse_trader_key

router = APIRouter(prefix="/api/v1", tags=["alerts"])
OWNER = "default"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_EVENTS = ["open", "increase", "decrease", "close", "flip", "liq_risk"]


def _emails(v: list[str]) -> list[str]:
    out = [x.strip() for x in v if x.strip()]
    bad = [x for x in out if not EMAIL_RE.match(x)]
    if bad:
        raise ValueError(f"invalid email: {', '.join(bad)}")
    return out


class SubscriptionIn(BaseModel):
    enabled: bool = True
    events: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENTS))
    min_notional: float = Field(0, ge=0)
    min_change_pct: float = Field(0, ge=0, le=100)
    coins: list[str] = Field(default_factory=list, max_length=50)
    recipients: list[str] = Field(default_factory=list, max_length=10)
    quiet_start: int | None = Field(None, ge=0, le=23)
    quiet_end: int | None = Field(None, ge=0, le=23)

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(EVENT_KINDS)
        if bad or not v:
            raise ValueError(f"events must be a non-empty subset of {EVENT_KINDS}")
        return v

    @field_validator("recipients")
    @classmethod
    def _recipients(cls, v: list[str]) -> list[str]:
        return _emails(v)


class SmtpIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(None, max_length=255, description="empty keeps the stored password")
    from_name: str = Field("AlphaScope", max_length=64)
    recipients: list[str] = Field(default_factory=list, max_length=10)
    public_url: str = Field("", max_length=255)

    @field_validator("recipients")
    @classmethod
    def _recipients(cls, v: list[str]) -> list[str]:
        return _emails(v)


class LiveIn(BaseModel):
    interval_seconds: int = Field(30, ge=10, le=3600)
    coalesce_seconds: int = Field(60, ge=0, le=3600)
    max_emails_per_hour: int = Field(30, ge=1, le=500)
    liq_risk_pct: float = Field(5.0, gt=0, le=50)
    timezone: str = Field("Asia/Shanghai", max_length=64)
    okx_interval_seconds: int = Field(60, ge=30, le=3600)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("unknown timezone") from exc
        return v


def _wallet_id(session: Session, address: str) -> int:
    address = parse_trader_key(address) or ''
    if not address:
        raise HTTPException(422, "invalid address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": address}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    return wid


@router.get("/alerts")
def list_alerts(session: Session = Depends(get_session)):
    subs = session.execute(text("""
        SELECT s.*, w.address, coalesce(a.alias, w.display_name) AS name, la.fetched_at AS last_polled,
               la.error AS last_error, la.account_value,
               (SELECT max(t) FROM position_event e WHERE e.wallet_id = s.wallet_id) AS last_event_at
        FROM alert_subscription s JOIN wallet w ON w.id = s.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        LEFT JOIN live_account la ON la.wallet_id = w.id
        WHERE s.owner = :o ORDER BY s.created_at DESC"""), {"o": OWNER}).mappings().all()
    return {"subscriptions": [{**dict(r), "min_notional": num(r["min_notional"]), "min_change_pct": num(r["min_change_pct"]),
                               "account_value": num(r["account_value"])} for r in subs],
            "event_kinds": EVENT_KINDS}


@router.get("/alerts/wallet/{address}")
def get_subscription(address: str, session: Session = Depends(get_session)):
    wid = _wallet_id(session, address)
    r = session.execute(text("SELECT * FROM alert_subscription WHERE wallet_id = :w AND owner = :o"),
                        {"w": wid, "o": OWNER}).mappings().first()
    smtp_ready = settings_store.has_secret(session, "smtp")
    if r is None:
        return {"subscribed": False, "smtp_ready": smtp_ready, "subscription": SubscriptionIn().model_dump()}
    return {"subscribed": True, "smtp_ready": smtp_ready, "subscription": {
        "enabled": r["enabled"], "events": list(r["events"]), "min_notional": num(r["min_notional"]),
        "min_change_pct": num(r["min_change_pct"]), "coins": list(r["coins"] or []),
        "recipients": list(r["recipients"] or []), "quiet_start": r["quiet_start"], "quiet_end": r["quiet_end"]}}


@router.put("/alerts/wallet/{address}")
def put_subscription(address: str, body: SubscriptionIn, session: Session = Depends(get_session)):
    wid = _wallet_id(session, address)
    session.execute(text("""
        INSERT INTO alert_subscription (owner, wallet_id, enabled, events, min_notional, min_change_pct, coins, recipients,
                                        quiet_start, quiet_end)
        VALUES (:o, :w, :en, :ev, :mn, :mp, :coins, :rc, :qs, :qe)
        ON CONFLICT (owner, wallet_id) DO UPDATE SET enabled = EXCLUDED.enabled, events = EXCLUDED.events,
          min_notional = EXCLUDED.min_notional, min_change_pct = EXCLUDED.min_change_pct, coins = EXCLUDED.coins,
          recipients = EXCLUDED.recipients, quiet_start = EXCLUDED.quiet_start, quiet_end = EXCLUDED.quiet_end"""),
        {"o": OWNER, "w": wid, "en": body.enabled, "ev": body.events, "mn": body.min_notional, "mp": body.min_change_pct,
         "coins": [c.strip() for c in body.coins if c.strip()], "rc": body.recipients, "qs": body.quiet_start,
         "qe": body.quiet_end})
    session.commit()
    return get_subscription(address, session)


@router.delete("/alerts/wallet/{address}")
def delete_subscription(address: str, session: Session = Depends(get_session)):
    wid = _wallet_id(session, address)
    session.execute(text("DELETE FROM alert_subscription WHERE wallet_id = :w AND owner = :o"), {"w": wid, "o": OWNER})
    session.commit()
    return {"ok": True}


@router.get("/alerts/events")
def recent_events(session: Session = Depends(get_session), limit: int = 100, address: str | None = None):
    where, params = "", {"o": OWNER, "n": min(max(limit, 1), 500)}
    if address:
        where = "WHERE e.wallet_id = :w"
        params["w"] = _wallet_id(session, address)
    rows = session.execute(text(f"""
        SELECT e.id, e.t, e.coin, e.kind, e.szi_before, e.szi_after, e.px, e.notional_change, e.notify_status,
               w.address, coalesce(a.alias, w.display_name) AS name
        FROM position_event e JOIN wallet w ON w.id = e.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        {where} ORDER BY e.t DESC, e.id DESC LIMIT :n"""), params).mappings().all()
    return {"events": [{**dict(r), **{k: num(r[k]) for k in ("szi_before", "szi_after", "px", "notional_change")}}
                       for r in rows]}


@router.get("/alerts/notifications")
def notifications(session: Session = Depends(get_session), limit: int = 50):
    rows = session.execute(text("""
        SELECT n.id, n.created_at, n.sent_at, n.status, n.attempts, n.error, n.subject, n.recipients, w.address
        FROM notification n LEFT JOIN wallet w ON w.id = n.wallet_id ORDER BY n.id DESC LIMIT :n"""),
        {"n": min(max(limit, 1), 200)}).mappings().all()
    today = session.execute(text("""
        SELECT count(*) FROM notification WHERE status = 'sent' AND sent_at >= date_trunc('day', now())""")).scalar_one()
    return {"notifications": [dict(r) for r in rows], "sent_today": today}


@router.get("/settings")
def get_settings_all(session: Session = Depends(get_session)):
    smtp = settings_store.get(session, "smtp")
    return {"smtp": {**smtp, "password_set": settings_store.has_secret(session, "smtp")},
            "live": settings_store.get(session, "live"),
            "live_status": settings_store.get(session, "live_status")}


@router.put("/settings/smtp")
def put_smtp(body: SmtpIn, session: Session = Depends(get_session)):
    value = body.model_dump(exclude={"password"})
    settings_store.put(session, "smtp", value, secret=body.password or None)
    session.commit()
    return get_settings_all(session)


@router.put("/settings/live")
def put_live(body: LiveIn, session: Session = Depends(get_session)):
    settings_store.put(session, "live", body.model_dump())
    session.commit()
    return get_settings_all(session)


@router.post("/settings/smtp/test")
def test_smtp(session: Session = Depends(get_session)):
    smtp = settings_store.get(session, "smtp")
    password = settings_store.get_secret(session, "smtp")
    if not password:
        raise HTTPException(422, "SMTP password is not set")
    recipients = list(smtp.get("recipients") or [])
    try:
        send(smtp, password, recipients, "【AlphaScope】测试邮件",
             "这是一封测试邮件：SMTP 配置正常，仓位提醒会发到这个地址。")
    except Exception as exc:  # noqa: BLE001 — report the SMTP error to the settings page
        raise HTTPException(502, f"send failed: {exc}") from exc
    return {"ok": True, "recipients": recipients}
