"""FastAPI dependencies: the web app forwards the session cookie as a bearer token; every authorization decision
is made here, never in the browser."""

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.auth.rbac import can
from nr.auth.sessions import CurrentUser, resolve
from nr.db import get_session


def current_user(authorization: str | None = Header(None), s: Session = Depends(get_session)) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "not signed in")
    user = resolve(s, authorization.removeprefix("Bearer ").strip(), datetime.now(UTC))
    if user is None:
        raise HTTPException(401, "session expired")
    return user


def active_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Signed in and past the forced password change."""
    if user.must_change_password:
        raise HTTPException(403, "password change required")
    return user


def require_roles(*roles: str):
    def dep(user: CurrentUser = Depends(active_user)) -> CurrentUser:
        if not user.roles & set(roles):
            raise HTTPException(403, "role not allowed")
        return user
    return dep


def account_perms(s: Session, user_id: int, account_id: int) -> set[str]:
    return set(s.execute(text("SELECT permission FROM account_permission WHERE user_id = :u AND account_id = :a"),
                         {"u": user_id, "a": account_id}).scalars())


def check_account(s: Session, user: CurrentUser, account_id: int, action: str) -> None:
    if not can(user.roles, account_perms(s, user.id, account_id), action):
        raise HTTPException(403, f"no {action} permission on this account")


def visible_accounts(s: Session, user: CurrentUser) -> list[int] | None:
    """None means every account."""
    if can(user.roles, set(), "view"):
        return None
    return list(s.execute(text("SELECT DISTINCT account_id FROM account_permission WHERE user_id = :u"),
                          {"u": user.id}).scalars())
