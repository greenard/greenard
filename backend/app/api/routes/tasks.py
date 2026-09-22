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


@router.get("/{task_id}/events")
def task_events(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Flux SSE de progression (text/event-stream) ; se termine quand la tâche est finie."""
    import json
    import time

    from fastapi.responses import StreamingResponse

    from app.db.session import SessionLocal

    get_task(task_id, user, db)  # contrôle d'accès

    def stream():
        last = None
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            with SessionLocal() as s:
                t = s.get(TaskLog, task_id)
                if t is None:
                    return
                payload = TaskOut.model_validate(t).model_dump(mode="json")
            if payload != last:
                yield f"data: {json.dumps(payload)}\n\n"
                last = payload
            if payload["status"] in ("success", "failure"):
                return
            yield ": keep-alive\n\n"
            time.sleep(0.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
