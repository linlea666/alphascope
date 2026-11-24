"""Star traders (docs/16): a short list the desk follows move by move. A star's move skips the editor-in-chief: it goes
straight to the star reporter for the square account of the star's venue, accepted and queued for drafting. The
draft still meets the fact check and the AI reviewer, the daily budget and the account's daily limit."""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.desk import assignment
from nr.desk.assignment import SQUARES
from nr.jobs.queue import enqueue

AGENT = "reporter_star"


def waiting_for_result(packet: dict | None, event: dict) -> bool:
    """A Binance close is told with its realized P&L: until the relay's history has it, the refresh rebuilds and
    routes again (a close is worth little without its result)."""
    if event["venue"] != "binance" or event["event_class"] != "whale_close":
        return False
    return not any(f["id"] == "close.realized_pnl_usd" for f in (packet or {}).get("facts", []))


def route(s: Session, event: dict, packet: dict | None, now: datetime) -> list[int]:
    ai = settings_store.get(s, "ai")
    if not ai["enabled"] or settings_store.emergency_stopped(s) or waiting_for_result(packet, event):
        return []
    merge = timedelta(minutes=settings_store.get(s, "stars")["merge_minutes"])
    out = []
    for acc in s.execute(text("""SELECT id FROM editorial_account WHERE status = 'active' AND platform = ANY(:p)
                                 AND :v = ANY(venues) ORDER BY id"""), {"p": list(SQUARES), "v": event["venue"]}).scalars():
        # moves in quick succession become one piece: the newer facts reach the open draft through the refresh
        busy = s.execute(text("""
            SELECT 1 FROM assignment a JOIN news_event e ON e.id = a.news_event_id
            WHERE a.account_id = :acc AND e.trader_id = :t AND a.source = 'star' AND a.created_at > :since
              AND a.status IN ('proposed', 'accepted', 'drafting') LIMIT 1"""),
            {"acc": acc, "t": event["trader_id"], "since": now - merge}).scalar()
        if busy:
            continue
        aid = assignment.create(s, event["id"], acc, source="star", reason="star trader", agent_key=AGENT)
        if aid is None:
            continue
        assignment.accept(s, aid, None)
        enqueue(s, "draft_generate", {"assignment_id": aid}, dedupe_key=f"draft:{aid}", priority=35, now=now)
        audit.record(s, "star.route", "assignment", aid, actor_kind="system", account_id=acc,
                     after={"event_id": event["id"], "trader_key": event["trader_key"]})
        out.append(aid)
    return out
