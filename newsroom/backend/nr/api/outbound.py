"""The outbound API for RPA and browser extensions (docs/15-publish-api.md): named tokens list ready posts, take one,
fetch its card, report the result and send back how the post did. Every publication they record carries the token's
channel; an expired or killed post is never handed out."""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now, one, rows
from nr.auth.deps import require_roles
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.publish import service as pub

router = APIRouter(prefix="/api", tags=["outbound"])
ADMIN = require_roles("admin")
CLAIM_FOR = timedelta(minutes=15)  # a claim nobody reported back on is released after this


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def token_of(s: Session, authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    t = one(s, "SELECT * FROM api_token WHERE token_hash = :h AND active",
            {"h": hash_token(authorization.removeprefix("Bearer ").strip())})
    if t is None:
        raise HTTPException(401, "unknown or disabled token")
    s.execute(text("UPDATE api_token SET last_used_at = now() WHERE id = :i"), {"i": t["id"]})
    return t


def scoped(t: dict, account_id: int) -> None:
    if t["account_ids"] and account_id not in t["account_ids"]:
        raise HTTPException(403, "this token is not allowed for that account")


READY = """
    SELECT a.id AS article_id, a.expires_at, a.claimed_by_token, a.claimed_at, acc.id AS account_id, acc.handle,
           acc.platform, a.language, v.body, v.thread, v.reply_text, v.style_key, ne.event_class, ne.coin,
           (SELECT m.id FROM media_asset m WHERE m.fact_packet_id = v.fact_packet_id AND m.template_key = v.card_template
              AND m.language = a.language AND m.kind = 'card_png' ORDER BY m.id DESC LIMIT 1) AS asset_id
    FROM article a JOIN article_version v ON v.id = a.current_version_id JOIN editorial_account acc ON acc.id = a.account_id
    JOIN news_event ne ON ne.id = a.news_event_id
    WHERE a.status = 'approved' AND a.deleted_at IS NULL AND acc.status = 'active'
      AND (a.expires_at IS NULL OR a.expires_at > :now)"""


def post(r: dict) -> dict:
    return {"article_id": r["article_id"], "account": {"id": r["account_id"], "handle": r["handle"],
                                                       "platform": r["platform"]},
            "language": r["language"], "posts": [r["body"], *(r["thread"] or [])], "reply": r["reply_text"],
            "image_url": f"/api/feed/assets/{r['asset_id']}" if r["asset_id"] else None,
            "expires_at": r["expires_at"], "style": r["style_key"], "event": {"class": r["event_class"], "coin": r["coin"]}}


@router.get("/feed/ready")
def ready_posts(account: str | None = Query(None, max_length=64), language: Literal["zh", "en"] | None = None,
                limit: int = Query(20, ge=1, le=100), authorization: str | None = Header(None),
                s: Session = Depends(get_session)):
    """Approved posts still in time and not taken by another token, soonest to expire first."""
    t = token_of(s, authorization)
    t_now = now()
    out = rows(s, READY + """
        AND (a.claimed_by_token IS NULL OR a.claimed_by_token = :tok OR a.claimed_at < :stale)
        AND (cardinality(CAST(:scope AS bigint[])) = 0 OR acc.id = ANY(:scope))
        AND (CAST(:acc AS text) IS NULL OR acc.handle = :acc OR CAST(acc.id AS text) = :acc)
        AND (CAST(:lang AS text) IS NULL OR a.language = :lang)
        ORDER BY a.expires_at NULLS LAST LIMIT :n""",
        {"now": t_now, "tok": t["id"], "stale": t_now - CLAIM_FOR, "scope": t["account_ids"] or [], "acc": account,
         "lang": language, "n": limit})
    s.commit()
    return {"posts": [post(r) for r in out], "as_of": t_now}


@router.post("/feed/{article_id}/claim")
def claim(article_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    """Take a post so no other robot sends it too. The claim lapses after 15 minutes without a result."""
    t = token_of(s, authorization)
    t_now = now()
    r = one(s, READY + " AND a.id = :a", {"now": t_now, "a": article_id})
    if r is None:
        raise HTTPException(409, "not ready: approved and in time posts only")
    scoped(t, r["account_id"])
    if r["claimed_by_token"] not in (None, t["id"]) and r["claimed_at"] and r["claimed_at"] > t_now - CLAIM_FOR:
        raise HTTPException(409, "another client has taken this post")
    s.execute(text("UPDATE article SET claimed_by_token = :t, claimed_at = :n WHERE id = :a"),
              {"t": t["id"], "n": t_now, "a": article_id})
    audit.record(s, "feed.claim", "article", article_id, actor_kind="system", account_id=r["account_id"],
                 after={"token": t["name"]})
    s.commit()
    return {"ok": True, "post": post(r), "claim_expires_at": t_now + CLAIM_FOR}


class ResultIn(BaseModel):
    ok: bool
    x_url: str | None = Field(None, max_length=300)
    reply_url: str | None = Field(None, max_length=300)
    published_at: datetime | None = None
    final_text: str | None = Field(None, max_length=8000)
    error: str | None = Field(None, max_length=1000)


@router.post("/feed/{article_id}/result")
def result(article_id: int, body: ResultIn, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    """Success records the publication (channel from the token); failure frees the post for another try."""
    t = token_of(s, authorization)
    a = one(s, "SELECT account_id, claimed_by_token FROM article WHERE id = :a", {"a": article_id})
    if a is None:
        raise HTTPException(404, "article not found")
    scoped(t, a["account_id"])
    if a["claimed_by_token"] not in (None, t["id"]):
        raise HTTPException(409, "another client holds this post")
    if not body.ok:
        s.execute(text("UPDATE article SET claimed_by_token = NULL, claimed_at = NULL WHERE id = :a"), {"a": article_id})
        audit.record(s, "feed.failed", "article", article_id, actor_kind="system", account_id=a["account_id"],
                     reason=(body.error or "")[:500], after={"token": t["name"]})
        s.commit()
        return {"ok": True, "requeued": True}
    pid = pub.mark_published(s, article_id, t["created_by"], body.x_url, body.published_at or now(), now(),
                             body.final_text, channel=t["channel"], reply_url=body.reply_url, api_token_id=t["id"])
    s.execute(text("UPDATE article SET claimed_by_token = NULL, claimed_at = NULL WHERE id = :a"), {"a": article_id})
    check = one(s, "SELECT matches_version, guard_report->>'status' AS guard FROM publication_record WHERE id = :p",
                {"p": pid})
    s.commit()
    return {"ok": True, "publication_id": pid, **check}


@router.get("/feed/assets/{asset_id}")
def asset(asset_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    """A card image, for posts this token may see."""
    t = token_of(s, authorization)
    m = one(s, """SELECT m.file_path, m.template_key, a.account_id FROM media_asset m
                  JOIN article a ON a.current_version_id IN (SELECT id FROM article_version WHERE fact_packet_id = m.fact_packet_id)
                  WHERE m.id = :i AND m.kind = 'card_png' AND a.status IN ('approved', 'published') LIMIT 1""", {"i": asset_id})
    if m is None:
        raise HTTPException(404, "image not found")
    scoped(t, m["account_id"])
    s.commit()
    return FileResponse(m["file_path"], media_type="image/png", filename=f"{m['template_key']}-{asset_id}.png")


class MetricsIn(BaseModel):
    impressions: int | None = Field(None, ge=0)
    likes: int | None = Field(None, ge=0)
    reposts: int | None = Field(None, ge=0)
    quotes: int | None = Field(None, ge=0)
    replies: int | None = Field(None, ge=0)
    bookmarks: int | None = Field(None, ge=0)
    profile_visits: int | None = Field(None, ge=0)
    follows: int | None = Field(None, ge=0)
    link_clicks: int | None = Field(None, ge=0)
    captured_at: datetime | None = None


@router.post("/feed/publications/{publication_id}/metrics")
def metrics(publication_id: int, body: MetricsIn, authorization: str | None = Header(None),
            s: Session = Depends(get_session)):
    """How a post did, read off the page by an extension: feeds the weekly report and the reviewer's calibration."""
    t = token_of(s, authorization)
    p = one(s, "SELECT account_id FROM publication_record WHERE id = :p", {"p": publication_id})
    if p is None:
        raise HTTPException(404, "publication not found")
    scoped(t, p["account_id"])
    d = body.model_dump(exclude={"captured_at"})
    s.execute(text(f"""INSERT INTO performance_snapshot (publication_id, captured_at, {", ".join(d)}, source)
                       VALUES (:p, :c, {", ".join(":" + k for k in d)}, 'api')"""),
              {"p": publication_id, "c": body.captured_at or now(), **d})
    s.commit()
    return {"ok": True}


# ---- token management ----------------------------------------------------------------------------------------------

class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    channel: Literal["rpa", "extension", "api"] = "extension"
    account_ids: list[int] = Field(default_factory=list)


@router.get("/api-tokens")
def list_tokens(s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    return rows(s, """SELECT id, name, channel, account_ids, active, created_at, last_used_at,
                             (SELECT count(*) FROM publication_record p WHERE p.api_token_id = t.id) AS publications
                      FROM api_token t ORDER BY id DESC""")


@router.post("/api-tokens")
def create_token(body: TokenIn, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    """The token is shown once; only its hash is stored."""
    raw = "nrt_" + secrets.token_urlsafe(32)
    tid = s.execute(text("""INSERT INTO api_token (name, token_hash, channel, account_ids, created_by)
                            VALUES (:n, :h, :c, :a, :u) RETURNING id"""),
                    {"n": body.name, "h": hash_token(raw), "c": body.channel, "a": body.account_ids, "u": user.id}).scalar_one()
    audit.record(s, "token.create", "api_token", tid, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"id": tid, "token": raw}


@router.post("/api-tokens/{token_id}/disable")
def disable_token(token_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(ADMIN)):
    s.execute(text("UPDATE api_token SET active = false WHERE id = :i"), {"i": token_id})
    audit.record(s, "token.disable", "api_token", token_id, user_id=user.id)
    s.commit()
    return {"ok": True}
