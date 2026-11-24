"""The star list (docs/16): traders the desk follows move by move. Binance lead traders come from the kolcue relay,
OKX lead traders from AlphaScope's feed. Adding a Binance star subscribes the relay to it on the next sync."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now, one, rows
from nr.auth.deps import active_user, require_roles
from nr.auth.sessions import CurrentUser
from nr.db import get_session
from nr.feed.client import FeedError, FeedUnavailable
from nr.jobs.handlers import default_deps
from nr.sources import relay

router = APIRouter(prefix="/api", tags=["stars"])
CHIEF = require_roles("admin", "editor_in_chief")
KEY = r"^(okx:[0-9]{6,24}|binance:[0-9A-Za-z_-]{1,64})$"


@router.get("/stars")
def list_stars(s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    stars = rows(s, """
        SELECT st.*, p.tags, p.stage, p.prev_stage, p.stage_since, p.editor_note, p.traits,
               (SELECT count(*) FROM publication_record pr JOIN article a ON a.id = pr.article_id
                  JOIN news_event e ON e.id = a.news_event_id JOIN trader t ON t.id = e.trader_id
                WHERE t.trader_key = st.trader_key AND pr.status = 'published') AS published,
               (SELECT count(*) FROM article a JOIN news_event e ON e.id = a.news_event_id JOIN trader t ON t.id = e.trader_id
                WHERE t.trader_key = st.trader_key AND a.status = 'approved' AND a.deleted_at IS NULL) AS ready
        FROM star_trader st LEFT JOIN trader_persona p ON p.trader_key = st.trader_key
        ORDER BY st.active DESC, st.id""")
    for st in stars:
        st["positions"] = rows(s, """SELECT symbol, side, size, entry_px, mark_px, upl, lever, notional, opened_at
                                     FROM relay_position WHERE trader_key = :k AND open ORDER BY notional DESC NULLS LAST""",
                               {"k": st["trader_key"]})
        st["closes"] = rows(s, """SELECT symbol, side, opened_at, closed_at, pnl, lever FROM relay_close
                                  WHERE trader_key = :k ORDER BY closed_at DESC LIMIT 8""", {"k": st["trader_key"]})
    relay_ok = one(s, "SELECT last_ok_at, last_error, last_error_at FROM source_connection WHERE name = 'kolcue_relay'")
    return {"stars": stars, "relay": relay_ok, "relay_configured": default_deps().relay() is not None}


@router.get("/stars/search")
def search(venue: Literal["binance", "okx"], q: str = Query("", max_length=64),
           s: Session = Depends(get_session), user: CurrentUser = Depends(active_user)):
    """Candidates to add: Binance lead traders the relay knows (by name), OKX traders AlphaScope knows (by nickname)."""
    have = set(s.execute(text("SELECT trader_key FROM star_trader")).scalars())
    deps = default_deps()
    if venue == "binance":
        client = deps.relay()
        if client is None:
            raise HTTPException(503, "the Binance relay is not configured (NEWSROOM_RELAY_URL / NEWSROOM_RELAY_SECRET)")
        try:
            with client:
                items = client.traders(q or None, page_size=50)["items"]
        except relay.RelayError as exc:
            raise HTTPException(502, f"relay: {exc}") from exc
        out = [{"trader_key": relay.key_of(x["trader_id"]), "name": x["name"], "active": x["active"],
                "intro": x.get("intro"), "avatar_url": x.get("avatar_url"), "stats_30d": x.get("upstream_stats")}
               for x in items]
    else:
        if not q:
            raise HTTPException(422, "type part of a nickname")
        try:
            with deps.feed() as c:
                d = c.research("okx_search", "/okx/traders", {"query": q, "limit": 30}, "rows", "okx_search.row")
        except (FeedUnavailable, FeedError) as exc:
            raise HTTPException(502, f"feed: {exc}") from exc
        out = [{"trader_key": r["trader_key"], "name": r["display_name"] or r["handle"] or r["trader_key"],
                "active": r["pool_status"] == "active", "asset": r["asset"],
                "stats_30d": {"rank": r["rank_30d"], "roi": r["pnl_ratio_30d"], "pnl": r["pnl_30d"]}} for r in d["rows"]]
    return [{**x, "added": x["trader_key"] in have} for x in out]


class StarIn(BaseModel):
    trader_key: str = Field(pattern=KEY)
    name: str = Field(min_length=1, max_length=64)
    note: str = Field("", max_length=500)
    min_notional_usd: float | None = Field(None, ge=0)
    avatar_url: str | None = Field(None, max_length=500, pattern=r"^https://")


@router.post("/stars")
def add_star(body: StarIn, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    venue = "binance" if body.trader_key.startswith("binance:") else "okx"
    sid = s.execute(text("""
        INSERT INTO star_trader (trader_key, venue, name, note, min_notional_usd, created_by)
        VALUES (:k, :v, :n, :note, :m, :u)
        ON CONFLICT (trader_key) DO UPDATE SET active = true, name = EXCLUDED.name, note = EXCLUDED.note,
          min_notional_usd = EXCLUDED.min_notional_usd RETURNING id"""),
        {"k": body.trader_key, "v": venue, "n": body.name, "note": body.note, "m": body.min_notional_usd,
         "u": user.id}).scalar_one()
    s.execute(text("""INSERT INTO trader (trader_key, venue, display_name, avatar_url) VALUES (:k, :v, :n, :a)
                      ON CONFLICT (trader_key) DO UPDATE SET display_name = coalesce(trader.display_name, EXCLUDED.display_name),
                        avatar_url = coalesce(EXCLUDED.avatar_url, trader.avatar_url)"""),
              {"k": body.trader_key, "v": venue, "n": body.name, "a": body.avatar_url})
    audit.record(s, "star.add", "star_trader", sid, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"id": sid}


class StarPatch(BaseModel):
    active: bool | None = None
    name: str | None = Field(None, min_length=1, max_length=64)
    note: str | None = Field(None, max_length=500)
    min_notional_usd: float | None = Field(None, ge=0)


@router.patch("/stars/{star_id}")
def update_star(star_id: int, body: StarPatch, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    sent = {k: getattr(body, k) for k in body.model_fields_set}
    if not sent:
        raise HTTPException(422, "nothing to change")
    n = s.execute(text(f"UPDATE star_trader SET {', '.join(f'{k} = :{k}' for k in sent)} WHERE id = :i"),
                  {**sent, "i": star_id}).rowcount
    if not n:
        raise HTTPException(404, "star not found")
    audit.record(s, "star.update", "star_trader", star_id, user_id=user.id, after=sent)
    s.commit()
    return {"ok": True}


@router.delete("/stars/{star_id}")
def remove_star(star_id: int, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    """Off the list: the relay stops polling it on the next sync; its stories and articles stay."""
    if not s.execute(text("DELETE FROM star_trader WHERE id = :i"), {"i": star_id}).rowcount:
        raise HTTPException(404, "star not found")
    audit.record(s, "star.remove", "star_trader", star_id, user_id=user.id)
    s.commit()
    return {"ok": True}


class PersonaIn(BaseModel):
    editor_note: str = Field("", max_length=1000)
    tags: list[str] | None = Field(None, max_length=8)


@router.put("/personas/{trader_key}")
def edit_persona(trader_key: str, body: PersonaIn, s: Session = Depends(get_session), user: CurrentUser = Depends(CHIEF)):
    """The editor's word on a character: a note the writer reads, and tags that replace the computed ones."""
    n = s.execute(text(f"""UPDATE trader_persona SET editor_note = :n, edited_by = :u, updated_at = :t
                           {", tags = CAST(:tags AS jsonb)" if body.tags is not None else ""} WHERE trader_key = :k"""),
                  {"n": body.editor_note, "u": user.id, "t": now(), "k": trader_key,
                   "tags": json.dumps([{"tag": t, "why": "editor"} for t in body.tags or []],
                                                    ensure_ascii=False)}).rowcount
    if not n:
        raise HTTPException(404, "no persona yet for this trader (it is built on the next persona refresh)")
    audit.record(s, "persona.edit", "trader_persona", trader_key, user_id=user.id, after=body.model_dump())
    s.commit()
    return {"ok": True}
