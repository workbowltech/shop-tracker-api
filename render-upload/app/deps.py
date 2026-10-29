"""Who is calling, and are they allowed? Checked on every request."""
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import ActivityLog, User
from .security import COOKIE_NAME, read_token

# Pages a user who must change their password can still use.
_ALLOWED_BEFORE_PASSWORD_CHANGE = {"/auth/me", "/auth/change-password", "/auth/logout"}


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    not_logged_in = HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")
    if not session:
        raise not_logged_in
    data = read_token(session)
    if not data:
        raise not_logged_in
    user = db.get(User, int(data["sub"]))
    if not user or not user.is_active or user.token_version != data.get("v"):
        raise not_logged_in
    if settings.force_password_change and user.must_change_password and request.url.path not in _ALLOWED_BEFORE_PASSWORD_CHANGE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Please change your password first.")
    return user


def require_roles(*roles: str):
    def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this.")
        return user

    return check


def log_activity(db: Session, user: User | None, action: str, entity: str, entity_id: str = "", detail: str = "") -> None:
    db.add(ActivityLog(user_id=user.id if user else None, action=action, entity=entity, entity_id=str(entity_id), detail=detail))
