from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user
from app.api.schemas import LoginIn, MeUpdate, PasswordChange, TokenOut, UserOut
from app.core.config import get_settings
from app.core.errors import AppError, Unauthorized
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.db.models import AuthSession, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "greenard_refresh"
COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER = "X-Requested-With"


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=s.refresh_token_days * 86400,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _issue(db: Session, user: User, request: Request, response: Response) -> TokenOut:
    s = get_settings()
    token, token_hash = new_refresh_token()
    db.add(
        AuthSession(
            user_id=user.id,
            refresh_token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days),
            user_agent=request.headers.get("user-agent", "")[:300],
            ip=request.client.host if request.client else "",
        )
    )
    _set_refresh_cookie(response, token)
    return TokenOut(access_token=create_access_token(user.id), expires_in=s.access_token_minutes * 60)


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError("PASSWORD_TOO_SHORT", "Password too short", min_length=MIN_PASSWORD_LENGTH)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    s = get_settings()
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(UTC)
    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise Unauthorized("AUTH_LOCKED", "Account temporarily locked", minutes=s.lockout_minutes)
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_logins = 0
            audit(db, user, "login_failed", "user", user.id)
            db.commit()
        raise Unauthorized("AUTH_INVALID_CREDENTIALS", "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    out = _issue(db, user, request, response)
    audit(db, user, "login", "user", user.id)
    db.commit()
    return out


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    # En-tête personnalisé : impossible à envoyer depuis un autre site sans pré-vérification CORS.
    if request.headers.get(CSRF_HEADER) != "greenard":
        raise Unauthorized("AUTH_CSRF", "Missing anti-CSRF header")
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise Unauthorized("AUTH_REQUIRED", "Authentication required")
    sess = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == hash_refresh_token(token)))
    now = datetime.now(UTC)
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        raise Unauthorized("AUTH_SESSION_EXPIRED", "Session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("AUTH_SESSION_EXPIRED", "Session expired")
    # Rotation : l'ancien jeton de rafraîchissement est révoqué.
    sess.revoked_at = now
    out = _issue(db, user, request, response)
    db.commit()
    return out


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        sess = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == hash_refresh_token(token)))
        if sess is not None and sess.revoked_at is None:
            sess.revoked_at = datetime.now(UTC)
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: MeUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.locale is not None:
        user.locale = body.locale
    db.commit()
    return user


@router.post("/change-password", status_code=204)
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    if not verify_password(user.password_hash, body.current_password):
        raise AppError("AUTH_INVALID_CREDENTIALS", "Invalid password")
    check_password_policy(body.new_password)
    user.password_hash = hash_password(body.new_password)
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
        sess.revoked_at = datetime.now(UTC)
    audit(db, user, "password_changed", "user", user.id)
    db.commit()
