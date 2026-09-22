from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import admin_user, audit, current_user
from app.api.schemas import TaskOut
from app.db.models import TaskLog, User
from app.db.session import get_db
from app.nwp import invariants
from app.nwp.catalog import MODELS, get_model
from app.tasks import jobs

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models(_: User = Depends(current_user)) -> list[dict]:
    out = []
    for spec in MODELS.values():
        d = spec.to_dict()
        d["invariants_status"] = invariants.status(spec.code)
        if d["invariants_status"].get("ready") and spec.regional:
            g = invariants.load(spec.code).grid  # géométrie réelle relue dans le GRIB
            d["domain"] = {"lat_min": g.lat_min, "lat_max": g.lat_max, "lon_min": g.lon_min, "lon_max": g.lon_max}
        out.append(d)
    return out


@router.post("/{code}/invariants", response_model=TaskOut, status_code=202)
def prepare_invariants(code: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)) -> TaskLog:
    get_model(code)
    t = TaskLog(kind="prepare_invariants", created_by=admin.id, message=code)
    db.add(t)
    audit(db, admin, "invariants_prepare", "model", code)
    db.commit()
    res = jobs.prepare_invariants.delay(t.id, code)
    t.celery_id = res.id
    db.commit()
    db.refresh(t)
    return t
