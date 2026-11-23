"""Opaque session tokens: the client holds the token, the database only its SHA-256."""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.auth.passwords import verify_password


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str | None
    roles: set[str] = field(default_factory=set)
    must_change_password: bool = False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(s: Session, username: str, password: str, now: datetime, max_failures: int,
                 lock_minutes: int) -> tuple[int | None, str | None]:
    """(user id, None) on success, else (None, reason). Failed attempts lock the account for a while."""
    u = s.execute(text("""SELECT id, password_hash, is_active, failed_logins, locked_until FROM app_user
                          WHERE lower(username) = lower(:u)"""), {"u": username}).mappings().first()
    if u is None or not u["is_active"]:
        verify_password(password, "scrypt$16384$8$1$00$00")  # same work either way: no username probing by timing
        return None, "invalid"
    if u["locked_until"] and u["locked_until"] > now:
        return None, "locked"
    if not verify_password(password, u["password_hash"]):
        fails = u["failed_logins"] + 1
        s.execute(text("""UPDATE app_user SET failed_logins = :f, locked_until = :l WHERE id = :i"""),
                  {"f": 0 if fails >= max_failures else fails, "i": u["id"],
                   "l": now + timedelta(minutes=lock_minutes) if fails >= max_failures else None})
        return None, "invalid"
    s.execute(text("UPDATE app_user SET failed_logins = 0, locked_until = NULL, last_login_at = :n WHERE id = :i"),
              {"n": now, "i": u["id"]})
    return u["id"], None


def create_session(s: Session, user_id: int, now: datetime, ttl_hours: int, ip: str | None = None,
                   user_agent: str | None = None) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=ttl_hours)
    s.execute(text("""INSERT INTO user_session (token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent)
                      VALUES (:h, :u, :n, :e, :n, :ip, :ua)"""),
              {"h": token_hash(token), "u": user_id, "n": now, "e": expires, "ip": ip,
               "ua": (user_agent or "")[:256]})
    return token, expires


def resolve(s: Session, token: str, now: datetime) -> CurrentUser | None:
    row = s.execute(text("""
        SELECT u.id, u.username, u.display_name, u.must_change_password, ss.id AS sid
        FROM user_session ss JOIN app_user u ON u.id = ss.user_id
        WHERE ss.token_hash = :h AND ss.revoked_at IS NULL AND ss.expires_at > :n AND u.is_active"""),
        {"h": token_hash(token), "n": now}).mappings().first()
    if row is None:
        return None
    roles = set(s.execute(text("SELECT role FROM user_role WHERE user_id = :u"), {"u": row["id"]}).scalars())
    return CurrentUser(row["id"], row["username"], row["display_name"], roles, row["must_change_password"])


def revoke(s: Session, token: str, now: datetime) -> None:
    s.execute(text("UPDATE user_session SET revoked_at = :n WHERE token_hash = :h AND revoked_at IS NULL"),
              {"n": now, "h": token_hash(token)})
