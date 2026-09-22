from dataclasses import asdict

from fastapi import APIRouter, Depends, File, UploadFile
from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import audit, current_user, require_project
from app.api.routes.geo import resolve_input
from app.api.schemas import GridPointsRequest, SelectionIn, SiteIn, SiteOut, TaskOut
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import ProjectRole, Site, SiteGridPoint, TaskLog, User
from app.db.session import get_db
from app.nwp.catalog import MODELS, get_model
from app.nwp.gridpoints import ALLOWED_N, domain_check, link_to_dict
from app.sites.importers import RowError, parse_file
from app.tasks import jobs

router = APIRouter(tags=["sites"])


def _point(lat: float, lon: float) -> WKTElement:
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def _get_site(db: Session, site_id: int, user: User, minimum: ProjectRole) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise NotFound("SITE_NOT_FOUND", "Site not found")
    require_project(db, site.project_id, user, minimum)
    return site


@router.get("/projects/{project_id}/sites", response_model=list[SiteOut])
def list_sites(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[Site]:
    require_project(db, project_id, user, ProjectRole.viewer)
    return list(db.scalars(select(Site).where(Site.project_id == project_id).order_by(Site.name)))


@router.post("/projects/{project_id}/sites", response_model=SiteOut, status_code=201)
def create_site(
    project_id: int, body: SiteIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Site:
    require_project(db, project_id, user, ProjectRole.engineer)
    lat, lon, info = resolve_input(body.lat, body.lon, body.x, body.y, body.crs)
    site = Site(
        project_id=project_id,
        name=body.name,
        lat=lat,
        lon=lon,
        geom=_point(lat, lon),
        input_crs=info["crs"],
        input_x=body.x,
        input_y=body.y,
    )
    db.add(site)
    db.flush()
    audit(db, user, "site_created", "site", site.id, project_id=project_id, lat=lat, lon=lon, crs=info["crs"])
    db.commit()
    return site


@router.post("/projects/{project_id}/sites/import")
async def import_sites(
    project_id: int,
    file: UploadFile = File(...),
    dry_run: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_project(db, project_id, user, ProjectRole.engineer)
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("IMPORT_FILE_TOO_LARGE", "File too large", max_mb=get_settings().max_upload_mb, status_code=413)
    result = parse_file(file.filename or "", data)
    existing = set(db.scalars(select(Site.name).where(Site.project_id == project_id)))
    for s in result.sites:
        if s.name in existing:
            result.errors.append(
                RowError(
                    s.row, "name", "IMPORT_NAME_EXISTS", "Site name already exists in project", params={"name": s.name}
                )
            )
    report = {
        "ok": result.ok,
        "sites": [asdict(s) for s in result.sites],
        "errors": [asdict(e) for e in result.errors],
        "created": [],
    }
    if not result.ok or dry_run:
        return report
    for s in result.sites:
        site = Site(
            project_id=project_id,
            name=s.name,
            lat=s.lat,
            lon=s.lon,
            geom=_point(s.lat, s.lon),
            input_crs=s.input_crs,
            input_x=s.input_x,
            input_y=s.input_y,
        )
        db.add(site)
        db.flush()
        report["created"].append(site.id)
    audit(db, user, "sites_imported", "project", project_id, filename=file.filename, count=len(result.sites))
    db.commit()
    return report


@router.get("/sites/{site_id}", response_model=SiteOut)
def get_site(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Site:
    return _get_site(db, site_id, user, ProjectRole.viewer)


@router.delete("/sites/{site_id}", status_code=204)
def delete_site(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    site = _get_site(db, site_id, user, ProjectRole.engineer)
    audit(db, user, "site_deleted", "site", site.id, name=site.name)
    db.delete(site)
    db.commit()


# ---- points de grille -------------------------------------------------------------------------


@router.get("/sites/{site_id}/model-availability")
def model_availability(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Disponibilité de chaque modèle pour le site (contrôle de domaine des modèles régionaux)."""
    site = _get_site(db, site_id, user, ProjectRole.viewer)
    out = []
    for code in MODELS:
        res = domain_check(code, site.lat, site.lon)
        out.append(
            {
                "model": code,
                "available": res is None,
                "message_code": res.message_code if res else "",
                "params": res.params if res else {},
            }
        )
    return out


@router.post("/sites/{site_id}/grid-points", response_model=TaskOut, status_code=202)
def compute_grid_points(
    site_id: int, body: GridPointsRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> TaskLog:
    site = _get_site(db, site_id, user, ProjectRole.engineer)
    for code in body.models:
        get_model(code)
    if body.method == "nearest" and body.n not in ALLOWED_N:
        raise AppError("GRID_N_INVALID", "n must be 4, 9 or 16", allowed=list(ALLOWED_N))
    t = TaskLog(
        kind="compute_grid_points", created_by=user.id, project_id=site.project_id, message=",".join(body.models)
    )
    db.add(t)
    db.commit()
    res = jobs.compute_grid_points.delay(t.id, site.id, body.models, body.n, body.method)
    t.celery_id = res.id
    db.commit()
    db.refresh(t)
    return t


@router.get("/sites/{site_id}/grid-points")
def list_grid_points(site_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    site = _get_site(db, site_id, user, ProjectRole.viewer)
    links = db.scalars(
        select(SiteGridPoint)
        .where(SiteGridPoint.site_id == site.id)
        .options(selectinload(SiteGridPoint.grid_point), selectinload(SiteGridPoint.site))
        .order_by(SiteGridPoint.model_code, SiteGridPoint.rank)
    )
    points = [link_to_dict(link) for link in links]
    return {
        "site": {"id": site.id, "lat": site.lat, "lon": site.lon, "dem_elevation_m": site.dem_elevation_m},
        "elevation_alert_m": get_settings().elevation_alert_m,
        "points": points,
    }


@router.patch("/sites/{site_id}/grid-points/{grid_point_id}")
def select_grid_point(
    site_id: int,
    grid_point_id: int,
    body: SelectionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    site = _get_site(db, site_id, user, ProjectRole.engineer)
    link = db.get(SiteGridPoint, (site.id, grid_point_id))
    if link is None:
        raise NotFound("GRID_POINT_NOT_FOUND", "Grid point not linked to this site")
    link.selected = body.selected
    audit(db, user, "grid_point_selected", "site", site.id, grid_point_id=grid_point_id, selected=body.selected)
    db.commit()
    return link_to_dict(link)
