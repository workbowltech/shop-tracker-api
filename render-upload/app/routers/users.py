"""Staff accounts — owner only (except the team list used for assigning job cards)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user, log_activity, require_roles
from ..models import TEAM_OF_ROLE, TEAMS, User
from ..schemas import TempPasswordOut, UserCreateIn, UserOut, UserUpdateIn
from ..security import hash_password, temporary_password

router = APIRouter(prefix="/users", tags=["staff"])
owner_only = require_roles("owner")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), me: User = Depends(owner_only)):
    return db.scalars(select(User).order_by(User.role, User.name)).all()


@router.get("/team/{team}", response_model=list[UserOut])
def list_team(team: str, db: Session = Depends(get_db), me: User = Depends(current_user)):
    """Active members of a team (to assign job cards). Staff can only list their own team."""
    if team not in TEAMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown team.")
    if me.role != "owner" and TEAM_OF_ROLE.get(me.role) != team:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this.")
    roles = [r for r, t in TEAM_OF_ROLE.items() if t == team]
    return db.scalars(select(User).where(User.role.in_(roles), User.is_active.is_(True)).order_by(User.name)).all()


@router.post("", response_model=TempPasswordOut, status_code=201)
def create_user(data: UserCreateIn, db: Session = Depends(get_db), me: User = Depends(owner_only)):
    if db.scalar(select(User).where(User.phone == data.phone)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A staff member with this mobile number already exists.")
    temp = temporary_password()
    user = User(name=data.name, phone=data.phone, role=data.role, password_hash=hash_password(temp), must_change_password=True)
    db.add(user)
    db.flush()
    log_activity(db, me, "create", "user", user.id, f"{user.name} ({user.role})")
    db.commit()
    return TempPasswordOut(user=UserOut.model_validate(user), temporary_password=temp)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: UserUpdateIn, db: Session = Depends(get_db), me: User = Depends(owner_only)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found.")
    if user.id == me.id and (data.is_active is False or (data.role and data.role != "owner")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate yourself or remove your own owner role.")
    changes = []
    if data.name is not None:
        user.name = " ".join(data.name.split())
        changes.append("name")
    if data.role is not None and data.role != user.role:
        user.role = data.role
        user.token_version += 1
        changes.append(f"role={data.role}")
    if data.is_active is not None and data.is_active != user.is_active:
        user.is_active = data.is_active
        user.token_version += 1  # deactivated → logged out at once
        changes.append("active" if data.is_active else "deactivated")
    log_activity(db, me, "update", "user", user.id, ", ".join(changes))
    db.commit()
    return user


@router.post("/{user_id}/reset-password", response_model=TempPasswordOut)
def reset_password(user_id: int, db: Session = Depends(get_db), me: User = Depends(owner_only)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found.")
    temp = temporary_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    user.token_version += 1
    log_activity(db, me, "reset_password", "user", user.id)
    db.commit()
    return TempPasswordOut(user=UserOut.model_validate(user), temporary_password=temp)
