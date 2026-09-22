"""Types d'éoliennes et parcs (layout)."""

import hashlib
import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, UploadFile
from geoalchemy2.elements import WKTElement
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import audit, current_user, require_project
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import ProjectRole, Turbine, TurbineType, User, WindFarm
from app.db.session import get_db
from app.farm import layout as layout_mod
from app.farm.turbines import TurbineSpec, parse_curve_csv, parse_wtg, validate
from app.terrain import dem

router = APIRouter(tags=["farm"])


async def _read(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("IMPORT_FILE_TOO_LARGE", "File too large", max_mb=get_settings().max_upload_mb, status_code=413)
    return data


def type_to_dict(t: TurbineType, used: int = 0) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "manufacturer": t.manufacturer,
        "rotor_d_m": t.rotor_d_m,
        "rated_kw": t.rated_kw,
        "hub_heights_m": t.hub_heights_m,
        "rho_ref": t.rho_ref,
        "cut_in_ms": t.cut_in_ms,
        "cut_out_ms": t.cut_out_ms,
        "restart_ms": t.restart_ms,
        "power_curve": t.power_curve,
        "density_curves": [{"rho": c["rho"], "n": len(c["points"])} for c in t.density_curves],
        "temp_derating": t.temp_derating,
        "source_format": t.source_format,
        "source_filename": t.source_filename,
        "turbines_using": used,
    }


class TypeMeta(BaseModel):
    name: str | None = Field(None, max_length=200)
    manufacturer: str | None = None
    rotor_d_m: float | None = None
    hub_heights_m: list[float] | None = None
    rho_ref: float | None = None
    cut_in_ms: float | None = None
    cut_out_ms: float | None = None
    restart_ms: float | None = None
    temp_derating: list[dict] | None = None


@router.get("/projects/{project_id}/turbine-types")
def list_types(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    require_project(db, project_id, user, ProjectRole.viewer)
    counts = dict(db.execute(select(Turbine.type_id, func.count()).group_by(Turbine.type_id)).all())
    return [
        type_to_dict(t, counts.get(t.id, 0))
        for t in db.scalars(select(TurbineType).where(TurbineType.project_id == project_id).order_by(TurbineType.name))
    ]


@router.post("/projects/{project_id}/turbine-types/import")
async def import_type(
    project_id: int,
    file: UploadFile = File(...),
    meta: str = Form("{}"),
    dry_run: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """`.wtg` WAsP ou CSV (ws, power_kw, ct) ; `meta` (JSON) complète ou corrige les caractéristiques."""
    require_project(db, project_id, user, ProjectRole.engineer)
    data = await _read(file)
    fname = file.filename or ""
    try:
        m = TypeMeta(**json.loads(meta or "{}"))
    except (ValueError, TypeError) as exc:
        raise AppError("TURBINE_META_INVALID", "Invalid metadata") from exc
    if fname.lower().endswith(".wtg"):
        spec = parse_wtg(data, fname)
    elif fname.lower().endswith((".csv", ".txt")):
        spec = TurbineSpec(
            name=m.name or fname.rsplit(".", 1)[0], power_curve=parse_curve_csv(data), source_format="csv"
        )
    else:
        raise AppError("IMPORT_UNSUPPORTED_FORMAT", "Use a WAsP .wtg or a CSV power / Ct curve")
    for k, v in m.model_dump(exclude_none=True).items():
        if k != "temp_derating":
            setattr(spec, k, v)
    issues = [asdict(i) for i in validate(spec)]
    exists = db.scalar(select(TurbineType).where(TurbineType.project_id == project_id, TurbineType.name == spec.name))
    if exists:
        issues.append(
            {
                "code": "TURBINE_NAME_EXISTS",
                "message": "Name already used",
                "severity": "error",
                "params": {"name": spec.name},
            }
        )
    ok = not any(i["severity"] == "error" for i in issues)
    preview = {
        "name": spec.name,
        "manufacturer": spec.manufacturer,
        "rotor_d_m": spec.rotor_d_m,
        "rated_kw": spec.rated_kw,
        "hub_heights_m": spec.hub_heights_m,
        "rho_ref": spec.rho_ref,
        "cut_in_ms": spec.cut_in_ms,
        "cut_out_ms": spec.cut_out_ms,
        "restart_ms": spec.restart_ms,
        "power_curve": spec.power_curve,
        "density_curves": [c["rho"] for c in spec.density_curves],
    }
    out = {"ok": ok, "issues": issues, "type": preview, "created": None}
    if not ok or dry_run:
        return out
    t = TurbineType(
        project_id=project_id,
        name=spec.name,
        manufacturer=spec.manufacturer,
        rotor_d_m=spec.rotor_d_m,
        rated_kw=spec.rated_kw,
        hub_heights_m=spec.hub_heights_m,
        rho_ref=spec.rho_ref,
        cut_in_ms=spec.cut_in_ms,
        cut_out_ms=spec.cut_out_ms,
        restart_ms=spec.restart_ms,
        power_curve=spec.power_curve,
        density_curves=spec.density_curves,
        temp_derating=m.temp_derating or [],
        source_format=spec.source_format,
        source_filename=fname,
        file_hash=hashlib.sha256(data).hexdigest(),
    )
    db.add(t)
    db.flush()
    audit(db, user, "turbine_type_imported", "turbine_type", t.id, name=t.name, file=fname)
    db.commit()
    out["created"] = t.id
    return out


@router.patch("/turbine-types/{type_id}")
def update_type(
    type_id: int, body: TypeMeta, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    t = db.get(TurbineType, type_id)
    if t is None:
        raise NotFound("TURBINE_TYPE_NOT_FOUND", "Turbine type not found")
    require_project(db, t.project_id, user, ProjectRole.engineer)
    changes = body.model_dump(exclude_none=True)
    spec = TurbineSpec(
        name=t.name,
        rotor_d_m=t.rotor_d_m,
        hub_heights_m=t.hub_heights_m,
        rho_ref=t.rho_ref,
        cut_in_ms=t.cut_in_ms,
        cut_out_ms=t.cut_out_ms,
        restart_ms=t.restart_ms,
        power_curve=t.power_curve,
    )
    for k, v in changes.items():
        if hasattr(spec, k):
            setattr(spec, k, v)
    errors = [asdict(i) for i in validate(spec) if i.severity == "error"]
    if errors:
        raise AppError("TURBINE_INVALID", "Invalid turbine characteristics", issues=errors)
    for k, v in changes.items():
        setattr(t, k, v)
    audit(db, user, "turbine_type_updated", "turbine_type", t.id, **changes)
    db.commit()
    return type_to_dict(t)


@router.delete("/turbine-types/{type_id}", status_code=204)
def delete_type(type_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    t = db.get(TurbineType, type_id)
    if t is None:
        raise NotFound("TURBINE_TYPE_NOT_FOUND", "Turbine type not found")
    require_project(db, t.project_id, user, ProjectRole.engineer)
    if db.scalar(select(func.count()).select_from(Turbine).where(Turbine.type_id == t.id)):
        raise AppError("TURBINE_TYPE_IN_USE", "Turbine type used in a layout", status_code=409)
    db.delete(t)
    db.commit()


# ---- parcs ---------------------------------------------------------------------------------------


class FarmIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    is_neighbour: bool = False


def farm_to_dict(f: WindFarm) -> dict:
    turbines = [
        {
            "id": t.id,
            "label": t.label,
            "lat": t.lat,
            "lon": t.lon,
            "input_crs": t.input_crs,
            "input_x": t.input_x,
            "input_y": t.input_y,
            "hub_height_m": t.hub_height_m,
            "type_id": t.type_id,
            "type_name": t.type.name,
            "dem_elevation_m": t.dem_elevation_m,
        }
        for t in f.turbines
    ]
    rated = sum(t.type.rated_kw for t in f.turbines) / 1000.0
    d = max((t.type.rotor_d_m for t in f.turbines), default=None)
    stats = layout_mod.spacing_stats([t.lat for t in f.turbines], [t.lon for t in f.turbines], d) if turbines else {}
    return {
        "id": f.id,
        "name": f.name,
        "is_neighbour": f.is_neighbour,
        "layout_filename": f.layout_filename,
        "n_turbines": len(turbines),
        "capacity_mw": round(rated, 2),
        "spacing": stats,
        "turbines": turbines,
    }


def _farms_query(project_id: int):
    return (
        select(WindFarm)
        .where(WindFarm.project_id == project_id)
        .options(selectinload(WindFarm.turbines).selectinload(Turbine.type))
        .order_by(WindFarm.name)
    )


@router.get("/projects/{project_id}/farms")
def list_farms(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    require_project(db, project_id, user, ProjectRole.viewer)
    return [farm_to_dict(f) for f in db.scalars(_farms_query(project_id))]


@router.post("/projects/{project_id}/farms", status_code=201)
def create_farm(
    project_id: int, body: FarmIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    require_project(db, project_id, user, ProjectRole.engineer)
    f = WindFarm(project_id=project_id, name=body.name, is_neighbour=body.is_neighbour)
    db.add(f)
    db.flush()
    audit(db, user, "farm_created", "wind_farm", f.id, name=f.name)
    db.commit()
    db.refresh(f)
    return farm_to_dict(f)


@router.delete("/farms/{farm_id}", status_code=204)
def delete_farm(farm_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    f = db.get(WindFarm, farm_id)
    if f is None:
        raise NotFound("FARM_NOT_FOUND", "Wind farm not found")
    require_project(db, f.project_id, user, ProjectRole.engineer)
    audit(db, user, "farm_deleted", "wind_farm", f.id, name=f.name)
    db.delete(f)
    db.commit()


@router.post("/farms/{farm_id}/layout/import")
async def import_layout(
    farm_id: int,
    file: UploadFile = File(...),
    default_crs: str = Form(""),
    dry_run: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Remplace le layout du parc (validation complète avant tout enregistrement)."""
    f = db.get(WindFarm, farm_id)
    if f is None:
        raise NotFound("FARM_NOT_FOUND", "Wind farm not found")
    require_project(db, f.project_id, user, ProjectRole.engineer)
    data = await _read(file)
    res = layout_mod.parse_file(file.filename or "", data, default_crs or None)
    types = {
        t.name.lower(): {"id": t.id, "rotor_d_m": t.rotor_d_m, "hub_heights_m": t.hub_heights_m}
        for t in db.scalars(select(TurbineType).where(TurbineType.project_id == f.project_id))
    }
    layout_mod.check_layout(res, types)
    report = {
        "ok": res.ok,
        "rows": [asdict(r) for r in res.rows],
        "issues": [asdict(i) for i in res.issues],
        "created": 0,
    }
    if not res.ok or dry_run:
        return report
    for t in list(f.turbines):
        db.delete(t)
    db.flush()
    for r in res.rows:
        try:
            elev = dem.sample(round(r.lat, 6), round(r.lon, 6))
            z = None if elev.sea_tile else elev.elevation_m
        except Exception:  # noqa: BLE001 — MNT injoignable : altitude laissée vide
            z = None
        db.add(
            Turbine(
                farm_id=f.id,
                label=r.label,
                lat=r.lat,
                lon=r.lon,
                geom=WKTElement(f"POINT({r.lon} {r.lat})", srid=4326),
                input_crs=r.input_crs,
                input_x=r.x,
                input_y=r.y,
                hub_height_m=r.hub_height_m,
                type_id=types[r.type_name.lower()]["id"],
                dem_elevation_m=z,
            )
        )
    f.layout_filename = file.filename or ""
    f.layout_hash = hashlib.sha256(data).hexdigest()
    audit(db, user, "layout_imported", "wind_farm", f.id, file=file.filename, n=len(res.rows))
    db.commit()
    report["created"] = len(res.rows)
    return report
