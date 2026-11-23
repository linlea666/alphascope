"""Assignments: which account covers an event, from which angle. Nothing is drafted for an account without an
accepted assignment, so an account the editor-in-chief kept silent, or an editor rejected, never gets a story."""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.events.states import InvalidTransition, transition


def account_fits(s: Session, account_id: int, event_id: int) -> tuple[bool, str]:
    a = s.execute(text("""SELECT acc.status, acc.allowed_event_classes, acc.min_score, ne.event_class, ne.score
                          FROM editorial_account acc, news_event ne WHERE acc.id = :a AND ne.id = :e"""),
                  {"a": account_id, "e": event_id}).mappings().first()
    if a is None:
        return False, "unknown account or event"
    if a["status"] != "active":
        return False, f"account is {a['status']}"
    if a["allowed_event_classes"] and a["event_class"] not in a["allowed_event_classes"]:
        return False, f"{a['event_class']} is outside this account's beat"
    if (a["score"] or 0) < a["min_score"]:
        return False, "score below this account's minimum"
    return True, ""


def create(s: Session, event_id: int, account_id: int, *, source: str, angle: str = "", reason: str = "",
           fmt: str = "single_post", eic_evaluation_id: int | None = None, needs_human: bool = False,
           status: str = "proposed", user_id: int | None = None) -> int | None:
    ok, why = account_fits(s, account_id, event_id)
    if not ok:
        if source == "editor":
            raise HTTPException(409, why)
        return None
    return s.execute(text("""
        INSERT INTO assignment (news_event_id, account_id, angle, reason, format, source, eic_evaluation_id, needs_human,
                                status, decided_by, decided_at)
        VALUES (:e, :a, :angle, :r, :f, :src, :eic, :nh, :st, :u, CASE WHEN :acc THEN now() END)
        ON CONFLICT (news_event_id, account_id) WHERE status NOT IN ('rejected', 'cancelled') DO NOTHING
        RETURNING id"""),
        {"e": event_id, "a": account_id, "angle": angle, "r": reason, "f": fmt, "src": source,
         "eic": eic_evaluation_id, "nh": needs_human, "st": status, "u": user_id, "acc": status == "accepted"}).scalar()


def accept(s: Session, assignment_id: int, user_id: int) -> int:
    """Accept an assignment: the event becomes assigned and an empty article is opened for the account."""
    a = s.execute(text("""SELECT asg.*, acc.language, ne.story_id, ne.state FROM assignment asg
                          JOIN editorial_account acc ON acc.id = asg.account_id
                          JOIN news_event ne ON ne.id = asg.news_event_id WHERE asg.id = :i FOR UPDATE OF asg"""),
                  {"i": assignment_id}).mappings().first()
    if a is None:
        raise HTTPException(404, "assignment not found")
    if a["status"] not in ("proposed", "accepted"):
        raise HTTPException(409, f"assignment is {a['status']}")
    if a["state"] in ("blocked", "dismissed", "declined", "archived", "detected"):
        raise HTTPException(409, f"event is {a['state']}")
    s.execute(text("UPDATE assignment SET status = 'accepted', decided_by = :u, decided_at = now() WHERE id = :i"),
              {"u": user_id, "i": assignment_id})
    if a["state"] in ("candidate", "evaluated"):
        try:
            transition(s, a["news_event_id"], "assigned", actor_kind="user", user_id=user_id, reason="assignment accepted")
        except InvalidTransition:
            pass
    art = s.execute(text("SELECT id FROM article WHERE assignment_id = :i"), {"i": assignment_id}).scalar()
    if art is None:
        art = s.execute(text("""
            INSERT INTO article (news_event_id, story_id, account_id, assignment_id, language, created_by)
            VALUES (:e, :st, :a, :asg, :l, :u) RETURNING id"""),
            {"e": a["news_event_id"], "st": a["story_id"], "a": a["account_id"], "asg": assignment_id,
             "l": a["language"], "u": user_id}).scalar_one()
    audit.record(s, "assignment.accept", "assignment", assignment_id, user_id=user_id, account_id=a["account_id"])
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
