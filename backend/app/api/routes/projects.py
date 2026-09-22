from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_RANK, audit, current_user, project_role, require_project
from app.api.schemas import MemberIn, MemberOut, ProjectIn, ProjectOut, ProjectUpdate
from app.core.errors import AppError, NotFound
from app.db.models import Project, ProjectMember, ProjectRole, Site, User
from app.db.session import get_db
from app.nwp.catalog import get_model

router = APIRouter(prefix="/projects", tags=["projects"])


def _out(db: Session, p: Project, role: ProjectRole) -> ProjectOut:
    count = db.scalar(select(func.count(Site.id)).where(Site.project_id == p.id)) or 0
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        display_tz=p.display_tz,
        created_at=p.created_at,
        role=role,
        site_count=count,
        archive_enabled=bool(p.archive_enabled),
        archive_models=p.archive_models or [],
        archive_max_lead_h=p.archive_max_lead_h or 168,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ProjectOut]:
    if user.is_admin:
        return [_out(db, p, ProjectRole.owner) for p in db.scalars(select(Project).order_by(Project.name))]
    rows = db.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.name)
    )
    return [_out(db, p, ProjectRole(role)) for p, role in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProjectOut:
    p = Project(name=body.name, description=body.description, display_tz=body.display_tz, created_by=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id, role=ProjectRole.owner.value))
    audit(db, user, "project_created", "project", p.id, name=p.name)
    db.commit()
    return _out(db, p, ProjectRole.owner)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProjectOut:
    p = require_project(db, project_id, user, ProjectRole.viewer)
    return _out(db, p, project_role(db, project_id, user))  # type: ignore[arg-type]


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, body: ProjectUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> ProjectOut:
    p = require_project(db, project_id, user, ProjectRole.owner)
    if body.archive_models is not None:
        for m in body.archive_models:
            get_model(m)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    audit(db, user, "project_updated", "project", p.id, **body.model_dump(exclude_unset=True))
    db.commit()
    return _out(db, p, project_role(db, project_id, user))  # type: ignore[arg-type]


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    p = require_project(db, project_id, user, ProjectRole.owner)
    audit(db, user, "project_deleted", "project", p.id, name=p.name)
    db.delete(p)
    db.commit()


# ---- membres ----------------------------------------------------------------------------------


@router.get("/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, project_id, user, ProjectRole.viewer)
    rows = db.execute(
        select(ProjectMember, User).join(User).where(ProjectMember.project_id == project_id).order_by(User.email)
    )
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=ProjectRole(m.role)) for m, u in rows]


@router.put("/{project_id}/members", response_model=MemberOut)
def upsert_member(
    project_id: int, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> MemberOut:
    require_project(db, project_id, user, ProjectRole.owner)
    target = db.scalar(select(User).where(User.email == body.email.lower()))
    if target is None or not target.is_active:
        raise NotFound("USER_NOT_FOUND", "User not found", email=body.email)
    m = db.get(ProjectMember, (project_id, target.id))
    if m is None:
        m = ProjectMember(project_id=project_id, user_id=target.id, role=body.role.value)
        db.add(m)
    else:
        _guard_last_owner(db, project_id, m, body.role)
        m.role = body.role.value
    audit(db, user, "member_set", "project", project_id, member=target.email, role=body.role.value)
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
def remove_member(
    project_id: int, user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> None:
    require_project(db, project_id, user, ProjectRole.owner)
    m = db.get(ProjectMember, (project_id, user_id))
    if m is None:
        raise NotFound("MEMBER_NOT_FOUND", "Member not found")
    _guard_last_owner(db, project_id, m, None)
    db.delete(m)
    audit(db, user, "member_removed", "project", project_id, user_id=user_id)
    db.commit()


def _guard_last_owner(db: Session, project_id: int, m: ProjectMember, new_role: ProjectRole | None) -> None:
    stays_owner = new_role is not None and ROLE_RANK[new_role] >= ROLE_RANK[ProjectRole.owner]
    if m.role != ProjectRole.owner.value or stays_owner:
        return
    owners = (
        db.scalar(
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.role == ProjectRole.owner.value)
        )
        or 0
    )
    if owners <= 1:
        raise AppError("PROJECT_LAST_OWNER", "A project must keep at least one owner")
