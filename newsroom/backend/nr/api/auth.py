from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import audit
from nr.api.common import now
from nr.auth.deps import current_user
from nr.auth.passwords import hash_password, password_problem, verify_password
from nr.auth.sessions import CurrentUser, authenticate, create_session, revoke
from nr.config import get_settings
from nr.db import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Login(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: Login, request: Request, s: Session = Depends(get_session)):
    st = get_settings()
    uid, why = authenticate(s, body.username, body.password, now(), st.login_max_failures, st.login_lock_minutes)
    if uid is None:
        s.commit()  # keep the failure counter
        raise HTTPException(423 if why == "locked" else 401, "account locked, try later" if why == "locked"
                            else "wrong username or password")
    token, expires = create_session(s, uid, now(), st.session_ttl_hours, request.client.host if request.client else None,
                                    request.headers.get("user-agent"))
    audit.record(s, "auth.login", "user", uid, user_id=uid)
    must = s.execute(text("SELECT must_change_password FROM app_user WHERE id = :u"), {"u": uid}).scalar()
    s.commit()
    return {"token": token, "expires_at": expires, "must_change_password": must}


@router.post("/logout")
def logout(authorization: str | None = Header(None), s: Session = Depends(get_session)):
    if authorization and authorization.startswith("Bearer "):
        revoke(s, authorization.removeprefix("Bearer ").strip(), now())
        s.commit()
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user), s: Session = Depends(get_session)):
    perms = s.execute(text("""SELECT account_id, array_agg(permission ORDER BY permission) FROM account_permission
                              WHERE user_id = :u GROUP BY account_id"""), {"u": user.id}).all()
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "roles": sorted(user.roles),
            "must_change_password": user.must_change_password, "account_permissions": {a: p for a, p in perms}}


class PasswordChange(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@router.post("/password")
def change_password(body: PasswordChange, user: CurrentUser = Depends(current_user), s: Session = Depends(get_session)):
    stored = s.execute(text("SELECT password_hash FROM app_user WHERE id = :u"), {"u": user.id}).scalar_one()
    if not verify_password(body.current, stored):
        raise HTTPException(403, "current password is wrong")
    if problem := password_problem(body.new):
        raise HTTPException(422, problem)
    s.execute(text("UPDATE app_user SET password_hash = :h, must_change_password = false WHERE id = :u"),
              {"h": hash_password(body.new), "u": user.id})
    audit.record(s, "auth.password_change", "user", user.id, user_id=user.id)
    s.commit()
    return {"ok": True}
