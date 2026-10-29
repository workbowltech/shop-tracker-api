"""Log in, log out, who am I, change password."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import current_user, log_activity
from ..models import LoginLog, User
from ..schemas import ChangePasswordIn, LoginIn, UserOut
from ..security import (
    as_utc,
    clear_session_cookie,
    hash_password,
    make_token,
    password_problem,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["login"])

WRONG = "Mobile number or password is wrong."


def _log(db: Session, request: Request, phone: str, user: User | None, ok: bool, reason: str) -> None:
    db.add(LoginLog(
        phone=phone, user_id=user.id if user else None, success=ok, reason=reason,
        ip=(request.client.host if request.client else "")[:64],
        user_agent=request.headers.get("user-agent", "")[:255],
    ))


@router.post("/login", response_model=UserOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    user = db.scalar(select(User).where(User.phone == data.phone))

    if user and as_utc(user.locked_until) and as_utc(user.locked_until) > now:
        _log(db, request, data.phone, user, False, "locked")
        db.commit()
        minutes = int((as_utc(user.locked_until) - now).total_seconds() // 60) + 1
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many wrong tries. Try again in {minutes} minutes.")

    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        if user and user.is_active:
            user.failed_attempts += 1
            if user.failed_attempts >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lock_minutes)
                user.failed_attempts = 0
        reason = "unknown_phone" if not user else ("inactive" if not user.is_active else "wrong_password")
        _log(db, request, data.phone, user, False, reason)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG)

    user.failed_attempts = 0
    user.locked_until = None
    _log(db, request, data.phone, user, True, "ok")
    db.commit()
    set_session_cookie(response, make_token(user.id, user.token_version))
    return user


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(data: ChangePasswordIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong.")
    problem = password_problem(data.new_password)
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if data.new_password == data.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be different from the current one.")
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    user.token_version += 1  # logs out other devices
    log_activity(db, user, "change_password", "user", user.id)
    db.commit()
    set_session_cookie(response, make_token(user.id, user.token_version))
    return user
