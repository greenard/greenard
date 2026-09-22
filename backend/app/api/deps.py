from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_access_token
from app.db.models import AuditLog, Project, ProjectMember, ProjectRole, User
from app.db.session import get_db

ROLE_RANK = {ProjectRole.viewer: 1, ProjectRole.engineer: 2, ProjectRole.owner: 3}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("AUTH_REQUIRED", "Authentication required")
    user_id = decode_access_token(auth[7:].strip())
    if user_id is None:
        raise Unauthorized("AUTH_TOKEN_INVALID", "Invalid or expired token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("AUTH_TOKEN_INVALID", "Invalid or expired token")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden("ADMIN_REQUIRED", "Administrator role required")
    return user


def project_role(db: Session, project_id: int, user: User) -> ProjectRole | None:
    if user.is_admin:
        return ProjectRole.owner
    m = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
    return ProjectRole(m.role) if m else None


def require_project(db: Session, project_id: int, user: User, minimum: ProjectRole) -> Project:
    project = db.get(Project, project_id)
    role = project_role(db, project_id, user) if project else None
    # 404 plutôt que 403 si l'utilisateur n'est pas membre : ne pas révéler l'existence du projet.
    if project is None or role is None:
        raise NotFound("PROJECT_NOT_FOUND", "Project not found", project_id=project_id)
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise Forbidden(
            "PROJECT_ROLE_INSUFFICIENT", "Insufficient project role", required=minimum.value, role=role.value
        )
    return project


def audit(
    db: Session, user: User | None, action: str, object_type: str = "", object_id: str | int = "", **payload
) -> None:
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            payload=payload,
        )
    )
