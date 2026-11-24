"""Assignments: which account covers an event, from which angle. Nothing is drafted for an account without an
accepted assignment, so an account the editor-in-chief kept silent, or an editor rejected, never gets a story."""

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit, settings_store
from nr.events.states import InvalidTransition, transition

SQUARES = ("okx_square", "binance_square")  # platforms where the story is the trader (docs/16)


def account_fits(s: Session, account_id: int, event_id: int, trial: bool = False) -> tuple[bool, str]:
    """A trial only needs the venue to match: paused accounts and low scores are fine for trying a model out."""
    a = s.execute(text("""SELECT acc.status, acc.allowed_event_classes, acc.min_score, acc.venues, acc.platform,
                                 ne.event_class, ne.score, ne.venue, ne.column_key,
                                 EXISTS (SELECT 1 FROM trader t JOIN star_trader st ON st.trader_key = t.trader_key
                                         WHERE t.id = ne.trader_id AND st.active) AS star,
                                 (SELECT max(abs((f->>'value')::numeric)) FROM fact_packet p,
                                         jsonb_array_elements(p.content->'facts') f
                                  WHERE p.id = ne.current_fact_packet_id
                                    AND f->>'id' IN ('close.realized_pnl_usd', 'close.pnl_estimate_usd',
                                                     'liquidation.realized_pnl_usd', 'liquidation.closed_pnl_usd')) AS result_usd
                          FROM editorial_account acc, news_event ne WHERE acc.id = :a AND ne.id = :e"""),
                  {"a": account_id, "e": event_id}).mappings().first()
    if a is None:
        return False, "unknown account or event"
    if a["status"] != "active" and not (trial and a["status"] == "paused"):
        return False, f"account is {a['status']}"
    # 'multi' (columns mixing venues) counts as Hyperliquid material: OKX-only material is for OKX Square accounts
    venue = "hyperliquid" if a["venue"] == "multi" else a["venue"]
    if a["venues"] and venue not in a["venues"]:
        return False, f"{a['venue']} material is outside this account's venues ({', '.join(a['venues'])})"
    if a["allowed_event_classes"] and a["event_class"] not in a["allowed_event_classes"]:
        return False, f"{a['event_class']} is outside this account's beat"
    if a["platform"] in SQUARES and a["column_key"] is None and not a["star"] and not trial:
        # on the squares a move is told through a person: a star's, a liquidation, or a big result
        big = Decimal(str(settings_store.get(s, "squares")["big_result_usd"]))
        if a["event_class"] != "liquidation" and not (a["event_class"] == "whale_close" and (a["result_usd"] or 0) >= big):
            return False, "square accounts carry a plain move only for star traders, liquidations and big results"
    if not trial and (a["score"] or 0) < a["min_score"]:
        return False, "score below this account's minimum"
    return True, ""


def square_agent(s: Session, account_id: int, event_id: int) -> str | None:
    """On the squares the writer is the star tracker for a star's move and the square storyteller for the rest."""
    r = s.execute(text("""SELECT acc.platform, EXISTS (SELECT 1 FROM trader t JOIN star_trader st ON st.trader_key = t.trader_key
                                                       WHERE t.id = ne.trader_id AND st.active) AS star, ne.column_key
                          FROM editorial_account acc, news_event ne WHERE acc.id = :a AND ne.id = :e"""),
                  {"a": account_id, "e": event_id}).mappings().first()
    if r is None or r["platform"] not in SQUARES:
        return None
    return "reporter_star" if r["star"] and r["column_key"] is None else "reporter_square"


def _default_agent(s: Session, event_id: int) -> str:
    from nr.ai.prompts import default_reporter
    cls = s.execute(text("SELECT event_class FROM news_event WHERE id = :e"), {"e": event_id}).scalar()
    return default_reporter(cls or "")


def create(s: Session, event_id: int, account_id: int, *, source: str, angle: str = "", reason: str = "",
           fmt: str = "single_post", eic_evaluation_id: int | None = None, needs_human: bool = False,
           status: str = "proposed", user_id: int | None = None, agent_key: str | None = None,
           style_key: str | None = None, model_config_id: int | None = None, due_at=None) -> int | None:
    ok, why = account_fits(s, account_id, event_id, trial=source == "trial")
    if not ok:
        if source in ("editor", "trial"):
            raise HTTPException(409, why)
        return None
    if agent_key is None or source in ("eic_ai", "star"):
        agent_key = square_agent(s, account_id, event_id) or agent_key
    return s.execute(text("""
        INSERT INTO assignment (news_event_id, account_id, angle, reason, format, source, eic_evaluation_id, needs_human,
                                status, decided_by, decided_at, agent_key, style_key, model_config_id, due_at)
        VALUES (:e, :a, :angle, :r, :f, :src, :eic, :nh, :st, :u, CASE WHEN :acc THEN now() END,
                :agent, :style, :model, :due)
        ON CONFLICT (news_event_id, account_id) WHERE status NOT IN ('rejected', 'cancelled') AND source <> 'trial'
        DO NOTHING RETURNING id"""),
        {"e": event_id, "a": account_id, "angle": angle, "r": reason, "f": fmt, "src": source,
         "eic": eic_evaluation_id, "nh": needs_human, "st": status, "u": user_id, "acc": status == "accepted",
         "agent": agent_key or _default_agent(s, event_id), "style": style_key, "model": model_config_id,
         "due": due_at}).scalar()


def accept(s: Session, assignment_id: int, user_id: int | None) -> int:
    """Accept an assignment: the event becomes assigned and an empty article is opened for the account. No user:
    the desk's autopilot accepted it."""
    actor = "user" if user_id else "ai"
    a = s.execute(text("""SELECT asg.*, acc.language, ne.story_id, ne.state FROM assignment asg
                          JOIN editorial_account acc ON acc.id = asg.account_id
                          JOIN news_event ne ON ne.id = asg.news_event_id WHERE asg.id = :i FOR UPDATE OF asg"""),
                  {"i": assignment_id}).mappings().first()
    if a is None:
        raise HTTPException(404, "assignment not found")
    if a["status"] not in ("proposed", "accepted"):
        raise HTTPException(409, f"assignment is {a['status']}")
    trial = a["source"] == "trial"
    if a["state"] == "archived" or (not trial and a["state"] in ("blocked", "dismissed", "declined", "detected")):
        raise HTTPException(409, f"event is {a['state']}")
    s.execute(text("UPDATE assignment SET status = 'accepted', decided_by = :u, decided_at = now() WHERE id = :i"),
              {"u": user_id, "i": assignment_id})
    if a["state"] in ("candidate", "evaluated") and not trial:  # a trial never moves the event along
        try:
            transition(s, a["news_event_id"], "assigned", actor_kind=actor, user_id=user_id, reason="assignment accepted")
        except InvalidTransition:
            pass
    art = s.execute(text("SELECT id FROM article WHERE assignment_id = :i"), {"i": assignment_id}).scalar()
    if art is None:
        art = s.execute(text("""
            INSERT INTO article (news_event_id, story_id, account_id, assignment_id, language, created_by)
            VALUES (:e, :st, :a, :asg, :l, :u) RETURNING id"""),
            {"e": a["news_event_id"], "st": a["story_id"], "a": a["account_id"], "asg": assignment_id,
             "l": a["language"], "u": user_id}).scalar_one()
    audit.record(s, "assignment.accept", "assignment", assignment_id, user_id=user_id, actor_kind=actor,
                 account_id=a["account_id"])
    return art


def reject(s: Session, assignment_id: int, user_id: int, reason: str) -> None:
    row = s.execute(text("""UPDATE assignment SET status = 'rejected', decided_by = :u, decided_at = now(),
                                   reason = reason || CASE WHEN :r <> '' THEN ' | rejected: ' || :r ELSE '' END
                            WHERE id = :i AND status IN ('proposed', 'accepted') RETURNING account_id"""),
                    {"u": user_id, "r": reason, "i": assignment_id}).scalar()
    if row is None:
        raise HTTPException(409, "assignment cannot be rejected")
    s.execute(text("""UPDATE article SET status = 'killed', updated_at = now()
                      WHERE assignment_id = :i AND status NOT IN ('published', 'retracted')"""), {"i": assignment_id})
    audit.record(s, "assignment.reject", "assignment", assignment_id, user_id=user_id, account_id=row, reason=reason)
