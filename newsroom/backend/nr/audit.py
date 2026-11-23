"""Append-only audit trail (the table rejects updates and deletes)."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def record(s: Session, action: str, object_type: str, object_id: Any = None, *, user_id: int | None = None,
           actor_kind: str = "user", account_id: int | None = None, before: Any = None, after: Any = None,
           reason: str | None = None) -> None:
    s.execute(text("""
        INSERT INTO audit_log (actor_kind, actor_user_id, action, object_type, object_id, account_id, before, after,
                               reason)
        VALUES (:k, :u, :a, :t, :o, :acc, CAST(:b AS jsonb), CAST(:af AS jsonb), :r)"""),
        {"k": "system" if actor_kind == "user" and user_id is None else actor_kind, "u": user_id, "a": action,
         "t": object_type, "o": None if object_id is None else str(object_id), "acc": account_id,
         "b": None if before is None else json.dumps(before, default=str),
         "af": None if after is None else json.dumps(after, default=str), "r": reason})
