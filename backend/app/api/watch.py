"""Watchlists (S1): grouped favourites, aliases, notes and tags for a single owner.

Write endpoints are only reachable through the web app's same-origin proxy (the API listens on localhost),
which checks the login and the request origin. Adding a wallet also queues its fills for archiving with the
highest priority, so favourites always get complete data.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.common import num
from app.config import get_settings
from app.db import get_session
from app.sources.leaderboard import ADDRESS_RE

router = APIRouter(prefix="/api/v1/watch", tags=["watch"])

OWNER = "default"
DEFAULT_GROUP = "默认"
WATCH_PRIORITY = 1_000_000  # above every swing candidate / macro-scan priority


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str | None = Field(None, max_length=16)
    sort: int | None = None


class MembershipIn(BaseModel):
    group_ids: list[int] = Field(default_factory=list, max_length=50)
    alias: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=2000)
    tags: list[str] | None = Field(None, max_length=20)


class AliasIn(BaseModel):
    alias: str = Field("", max_length=64)
    note: str | None = Field(None, max_length=2000)


def _wallet_id(session: Session, address: str) -> int:
    address = address.lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(422, "invalid address")
    wid = session.execute(text("SELECT id FROM wallet WHERE address = :a"), {"a": address}).scalar()
    if wid is None:
        raise HTTPException(404, "wallet not in database")
    return wid


def _ensure_default_group(session: Session) -> int:
    gid = session.execute(text("SELECT id FROM watch_group WHERE owner = :o ORDER BY sort, id LIMIT 1"),
                          {"o": OWNER}).scalar()
    if gid is None:
        gid = session.execute(text("""
            INSERT INTO watch_group (owner, name, sort) VALUES (:o, :n, 0) RETURNING id"""),
            {"o": OWNER, "n": DEFAULT_GROUP}).scalar_one()
    return gid


def _queue_fills(session: Session, wallet_id: int) -> None:
    start = datetime.fromisoformat(get_settings().swing_fill_start).replace(tzinfo=UTC)
    session.execute(text("""
        INSERT INTO fill_sync (wallet_id, reason, priority, requested_start, status, n_fills, queued_at)
        VALUES (:w, 'watchlist', :p, :s, 'queued', 0, now())
        ON CONFLICT (wallet_id) DO UPDATE SET priority = greatest(fill_sync.priority, EXCLUDED.priority),
          requested_start = least(fill_sync.requested_start, EXCLUDED.requested_start)"""),
        {"w": wallet_id, "p": WATCH_PRIORITY, "s": start})


@router.get("")
def watchlist(session: Session = Depends(get_session)):
    _ensure_default_group(session)
    session.commit()
    groups = session.execute(text("""
        SELECT g.id, g.name, g.color, g.sort, count(i.id) AS n FROM watch_group g
        LEFT JOIN watch_item i ON i.group_id = g.id WHERE g.owner = :o GROUP BY g.id ORDER BY g.sort, g.id"""),
        {"o": OWNER}).mappings().all()
    items = session.execute(text("""
        SELECT i.id AS item_id, i.group_id, i.note, i.tags, i.created_at, w.address, w.display_name,
               a.alias, w.account_value, w.week_pnl, w.month_pnl, w.tier, w.quality_flags || w.enrichment_flags AS flags,
               f.status AS sync_status, f.n_fills,
               (SELECT max(edge_shrunk) FROM timing_score t WHERE t.wallet_id = w.id AND t.status = 'scored'
                  AND t.coin_group = 'all' AND t.q_value < 0.1) AS best_edge,
               (SELECT jsonb_agg(jsonb_build_object('coin', p.coin, 'szi', p.szi, 'value', p.position_value)
                                 ORDER BY abs(p.position_value) DESC)
                  FROM position_snapshot p WHERE p.account_snapshot_id =
                    (SELECT s.id FROM account_snapshot s WHERE s.wallet_id = w.id ORDER BY s.fetched_at DESC LIMIT 1)
               ) AS positions
        FROM watch_item i JOIN watch_group g ON g.id = i.group_id JOIN wallet w ON w.id = i.wallet_id
        LEFT JOIN wallet_alias a ON a.wallet_id = w.id AND a.owner = :o
        LEFT JOIN fill_sync f ON f.wallet_id = w.id
        WHERE g.owner = :o ORDER BY i.created_at DESC"""), {"o": OWNER}).mappings().all()
    return {
        "groups": [dict(g) for g in groups],
        "items": [{**dict(i), "account_value": num(i["account_value"]), "week_pnl": num(i["week_pnl"]),
                   "month_pnl": num(i["month_pnl"]), "best_edge": num(i["best_edge"]),
                   "flags": list(i["flags"] or []), "tags": list(i["tags"] or []),
                   "positions": (i["positions"] or [])[:4]} for i in items],
    }


@router.get("/aliases")
def aliases(session: Session = Depends(get_session)):
    """address -> alias for every aliased wallet (the web app shows aliases everywhere)."""
    rows = session.execute(text("""
        SELECT w.address, a.alias FROM wallet_alias a JOIN wallet w ON w.id = a.wallet_id
        WHERE a.owner = :o AND a.alias <> ''"""), {"o": OWNER}).all()
    return {"aliases": {r.address: r.alias for r in rows}}


@router.get("/wallet/{address}")
def wallet_membership(address: str, session: Session = Depends(get_session)):
    wid = _wallet_id(session, address)
    _ensure_default_group(session)
    session.commit()
    groups = session.execute(text("""
        SELECT g.id, g.name, g.color, (i.id IS NOT NULL) AS member FROM watch_group g
        LEFT JOIN watch_item i ON i.group_id = g.id AND i.wallet_id = :w
        WHERE g.owner = :o ORDER BY g.sort, g.id"""), {"w": wid, "o": OWNER}).mappings().all()
    alias = session.execute(text("SELECT alias, note FROM wallet_alias WHERE wallet_id = :w AND owner = :o"),
                            {"w": wid, "o": OWNER}).mappings().first()
    item = session.execute(text("""
        SELECT i.note, i.tags FROM watch_item i JOIN watch_group g ON g.id = i.group_id
        WHERE i.wallet_id = :w AND g.owner = :o ORDER BY i.created_at LIMIT 1"""),
        {"w": wid, "o": OWNER}).mappings().first()
    return {"groups": [dict(g) for g in groups], "alias": alias["alias"] if alias else "",
            "note": (item or {}).get("note") or (alias or {}).get("note") or "",
            "tags": list((item or {}).get("tags") or [])}


@router.put("/wallet/{address}")
def set_membership(address: str, body: MembershipIn, session: Session = Depends(get_session)):
    """Set exactly which groups the wallet belongs to (empty = not watched), plus alias / note / tags."""
    wid = _wallet_id(session, address)
    valid = set(session.execute(text("SELECT id FROM watch_group WHERE owner = :o"), {"o": OWNER}).scalars())
    wanted = set(body.group_ids)
    if wanted - valid:
        raise HTTPException(422, f"unknown groups {sorted(wanted - valid)}")
    session.execute(text("""
        DELETE FROM watch_item i USING watch_group g WHERE g.id = i.group_id AND g.owner = :o AND i.wallet_id = :w
          AND NOT (i.group_id = ANY(:keep))"""), {"o": OWNER, "w": wid, "keep": list(wanted)})
    for gid in wanted:
        session.execute(text("""
            INSERT INTO watch_item (group_id, wallet_id, note, tags) VALUES (:g, :w, :n, :t)
            ON CONFLICT (group_id, wallet_id) DO UPDATE SET note = coalesce(EXCLUDED.note, watch_item.note),
              tags = CASE WHEN :tags_given THEN EXCLUDED.tags ELSE watch_item.tags END"""),
            {"g": gid, "w": wid, "n": body.note, "t": body.tags or [], "tags_given": body.tags is not None})
    if body.alias is not None:
        _set_alias(session, wid, body.alias, body.note)
    if wanted:
        _queue_fills(session, wid)
    session.commit()
    return wallet_membership(address, session)


def _set_alias(session: Session, wid: int, alias: str, note: str | None) -> None:
    alias = alias.strip()
    if not alias:
        session.execute(text("DELETE FROM wallet_alias WHERE wallet_id = :w AND owner = :o"), {"w": wid, "o": OWNER})
        return
    session.execute(text("""
        INSERT INTO wallet_alias (wallet_id, owner, alias, note, updated_at) VALUES (:w, :o, :a, :n, now())
        ON CONFLICT (wallet_id, owner) DO UPDATE SET alias = EXCLUDED.alias,
          note = coalesce(EXCLUDED.note, wallet_alias.note), updated_at = now()"""),
        {"w": wid, "o": OWNER, "a": alias, "n": note})


@router.put("/alias/{address}")
def set_alias(address: str, body: AliasIn, session: Session = Depends(get_session)):
    wid = _wallet_id(session, address)
    _set_alias(session, wid, body.alias, body.note)
    session.commit()
    return {"ok": True}


@router.post("/groups")
def create_group(body: GroupIn, session: Session = Depends(get_session)):
    exists = session.execute(text("SELECT 1 FROM watch_group WHERE owner = :o AND name = :n"),
                             {"o": OWNER, "n": body.name.strip()}).scalar()
    if exists:
        raise HTTPException(409, "group name already exists")
    sort = body.sort if body.sort is not None else (session.execute(text(
        "SELECT coalesce(max(sort), 0) + 1 FROM watch_group WHERE owner = :o"), {"o": OWNER}).scalar_one())
    gid = session.execute(text("""
        INSERT INTO watch_group (owner, name, color, sort) VALUES (:o, :n, :c, :s) RETURNING id"""),
        {"o": OWNER, "n": body.name.strip(), "c": body.color, "s": sort}).scalar_one()
    session.commit()
    return {"id": gid}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, body: GroupIn, session: Session = Depends(get_session)):
    n = session.execute(text("""
        UPDATE watch_group SET name = :n, color = coalesce(:c, color), sort = coalesce(:s, sort)
        WHERE id = :id AND owner = :o"""),
        {"n": body.name.strip(), "c": body.color, "s": body.sort, "id": group_id, "o": OWNER}).rowcount
    if not n:
        raise HTTPException(404, "group not found")
    session.commit()
    return {"ok": True}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, session: Session = Depends(get_session)):
    count = session.execute(text("SELECT count(*) FROM watch_group WHERE owner = :o"), {"o": OWNER}).scalar_one()
    if count <= 1:
        raise HTTPException(409, "the last group cannot be deleted")
    n = session.execute(text("DELETE FROM watch_group WHERE id = :id AND owner = :o"),
                        {"id": group_id, "o": OWNER}).rowcount
    if not n:
        raise HTTPException(404, "group not found")
    session.commit()
    return {"ok": True}
