from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_project
from app.api.schemas import TaskOut
from app.core.errors import NotFound
from app.db.models import ProjectRole, TaskLog, User
from app.db.session import get_db

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> TaskLog:
    t = db.get(TaskLog, task_id)
    if t is None:
        raise NotFound("TASK_NOT_FOUND", "Task not found")
    if t.project_id is not None:
        require_project(db, t.project_id, user, ProjectRole.viewer)
    elif not user.is_admin and t.created_by != user.id:
        raise NotFound("TASK_NOT_FOUND", "Task not found")
    return t
