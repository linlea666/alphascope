"""News event state machine (docs/08-editorial-workflow.md). The same list is seeded into event_state_transition by
migration 0003; a test keeps them identical."""

from sqlalchemy import text
from sqlalchemy.orm import Session

STATES = ("detected", "blocked", "candidate", "dismissed", "evaluating", "evaluated", "assigned", "declined",
          "covered", "archived")
TRANSITIONS = {
    ("detected", "candidate"), ("detected", "blocked"), ("detected", "dismissed"),
    ("blocked", "candidate"), ("blocked", "dismissed"), ("blocked", "archived"),
    ("candidate", "blocked"), ("candidate", "evaluating"), ("candidate", "assigned"), ("candidate", "declined"),
    ("candidate", "dismissed"), ("candidate", "archived"),
    ("evaluating", "evaluated"), ("evaluating", "candidate"), ("evaluating", "blocked"),
    ("evaluated", "assigned"), ("evaluated", "declined"), ("evaluated", "candidate"), ("evaluated", "blocked"),
    ("evaluated", "archived"),
    ("assigned", "covered"), ("assigned", "blocked"), ("assigned", "declined"), ("assigned", "archived"),
    ("covered", "archived"), ("declined", "candidate"), ("declined", "archived"),
    ("dismissed", "candidate"), ("dismissed", "archived"),
}
# an event in these states may still change before anything is published about it
OPEN_STATES = ("detected", "blocked", "candidate", "dismissed", "evaluating", "evaluated", "assigned")


class InvalidTransition(Exception):
    pass


def transition(s: Session, event_id: int, to: str, *, actor_kind: str = "system", user_id: int | None = None,
               reason: str | None = None, allow_same: bool = False) -> str:
    """Move an event to `to`; returns the previous state. Raises InvalidTransition when not allowed."""
    cur = s.execute(text("SELECT state FROM news_event WHERE id = :i FOR UPDATE"), {"i": event_id}).scalar_one()
    if cur == to and allow_same:
        return cur
    if (cur, to) not in TRANSITIONS:
        raise InvalidTransition(f"{cur} -> {to}")
    s.execute(text("UPDATE news_event SET state = :t, state_changed_at = now(), updated_at = now() WHERE id = :i"),
              {"t": to, "i": event_id})
    s.execute(text("""INSERT INTO event_state_history (news_event_id, from_state, to_state, actor_kind, actor_user_id,
                                                         reason)
                      VALUES (:i, :f, :t, :k, :u, :r)"""),
              {"i": event_id, "f": cur, "t": to, "k": actor_kind, "u": user_id, "r": reason})
    return cur
