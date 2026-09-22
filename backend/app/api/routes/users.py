from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_user, audit
from app.api.routes.auth import check_password_policy
from app.api.schemas import UserCreate, UserOut, UserUpdate
from app.core.errors import AppError, NotFound
from app.core.security import hash_password
from app.db.models import AuthSession, User
from app.db.session import get_db

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(_: User = Depends(admin_user), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.email)))


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> User:
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise AppError("USER_EMAIL_EXISTS", "Email already registered", email=email, status_code=409)
    check_password_policy(body.password)
    user = User(
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
        locale=body.locale,
    )
    db.add(user)
    db.flush()
    audit(db, admin, "user_created", "user", user.id, email=email, is_admin=body.is_admin)
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("USER_NOT_FOUND", "User not found")
    if user.id == admin.id and (body.is_admin is False or body.is_active is False):
        raise AppError("USER_CANNOT_DEMOTE_SELF", "Administrators cannot demote or deactivate themselves")
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    for k, v in changes.items():
        setattr(user, k, v)
    if body.password:
        check_password_policy(body.password)
        user.password_hash = hash_password(body.password)
        changes["password"] = "reset"
    if body.password or body.is_active is False:
        for sess in db.scalars(
            select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        ):
            sess.revoked_at = datetime.now(UTC)
    audit(db, admin, "user_updated", "user", user.id, **changes)
    db.commit()
    return user
