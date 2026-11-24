"""Assignments: which account covers an event, from which angle. Nothing is drafted for an account without an
accepted assignment, so an account the editor-in-chief kept silent, or an editor rejected, never gets a story."""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.events.states import InvalidTransition, transition


def account_fits(s: Session, account_id: int, event_id: int, trial: bool = False) -> tuple[bool, str]:
    """A trial only needs the venue to match: paused accounts and low scores are fine for trying a model out."""
    a = s.execute(text("""SELECT acc.status, acc.allowed_event_classes, acc.min_score, acc.venues, acc.platform,
                                 ne.event_class, ne.score, ne.venue
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
    if not trial and (a["score"] or 0) < a["min_score"]:
        return False, "score below this account's minimum"
    return True, ""


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
