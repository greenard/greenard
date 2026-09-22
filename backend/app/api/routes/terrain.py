"""Terrain du projet : MNT, occupation du sol → rugosité, carte WAsP .map ; aperçus pour la carte."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import audit, current_user, require_project
from app.api.routes.farm import _read
from app.api.schemas import TaskOut
from app.core.errors import AppError, NotFound
from app.db.models import ProjectRole, TaskLog, TerrainLayer, User
from app.db.session import get_db
from app.geo.crs import normalize_crs
from app.tasks import jobs
from app.terrain import rasters

router = APIRouter(tags=["terrain"])


def layer_to_dict(t: TerrainLayer) -> dict:
    return {
        "id": t.id,
        "kind": t.kind,
        "name": t.name,
        "source": t.source,
        "crs": t.crs,
        "resolution_m": t.resolution_m,
        "bbox": t.bbox,
        "z0_table": t.z0_table,
        "stats": t.stats,
        "created_at": t.created_at,
    }


def _layer(db: Session, layer_id: int, user: User, minimum=ProjectRole.viewer) -> TerrainLayer:
    t = db.get(TerrainLayer, layer_id)
    if t is None:
        raise NotFound("TERRAIN_LAYER_NOT_FOUND", "Terrain layer not found")
    require_project(db, t.project_id, user, minimum)
    return t


@router.get("/projects/{project_id}/terrain")
def list_layers(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    require_project(db, project_id, user, ProjectRole.viewer)
    rows = db.scalars(select(TerrainLayer).where(TerrainLayer.project_id == project_id).order_by(TerrainLayer.id))
    return {"layers": [layer_to_dict(t) for t in rows], "default_z0_table": rasters.DEFAULT_Z0}


class DownloadIn(BaseModel):
    kind: str = Field(pattern="^(dem|landcover)$")
    margin_km: float = Field(5.0, ge=0.5, le=30.0)


@router.post("/projects/{project_id}/terrain/download", response_model=TaskOut, status_code=202)
def download(project_id: int, body: DownloadIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """MNT Copernicus GLO-30 ou occupation du sol ESA WorldCover (+ rugosité) sur l'emprise du projet
    (éoliennes, mâts, sites) élargie de `margin_km`."""
    require_project(db, project_id, user, ProjectRole.engineer)
    t = TaskLog(kind=f"terrain_{body.kind}", created_by=user.id, project_id=project_id, message=body.kind)
    db.add(t)
    db.commit()
    res = jobs.terrain_download.delay(t.id, project_id, body.kind, body.margin_km)
    t.celery_id = res.id
    db.commit()
    db.refresh(t)
    return t


@router.post("/projects/{project_id}/terrain/upload-dem", status_code=201)
async def upload_dem(
    project_id: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    require_project(db, project_id, user, ProjectRole.engineer)
    data = await _read(file)
    layer = TerrainLayer(
        project_id=project_id, kind="dem", name=file.filename or "dem.tif", source="upload", file_uri=""
    )
    db.add(layer)
    db.flush()
    out = rasters.terrain_dir(project_id) / f"dem_{layer.id}.tif"
    info = rasters.upload_geotiff(data, out)
    layer.file_uri, layer.crs, layer.bbox, layer.stats = str(out), info["crs"], info["bbox"], info
    layer.resolution_m = round(info["res_deg"] * 111_320, 1)
    audit(db, user, "terrain_uploaded", "terrain_layer", layer.id, file=file.filename)
    db.commit()
    return layer_to_dict(layer)


@router.post("/projects/{project_id}/terrain/wasp-map", status_code=201)
async def upload_map(
    project_id: int,
    file: UploadFile = File(...),
    crs: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Carte WAsP `.map` (le format ne contient pas de système de coordonnées : à préciser)."""
    require_project(db, project_id, user, ProjectRole.engineer)
    code = normalize_crs(crs)
    parsed = rasters.parse_wasp_map(await _read(file))
    gj = rasters.map_to_geojson(parsed, code)
    layer = TerrainLayer(
        project_id=project_id,
        kind="roughness_map",
        name=file.filename or "map",
        source="wasp_map",
        file_uri="",
        crs=code,
    )
    db.add(layer)
    db.flush()
    out = rasters.terrain_dir(project_id) / f"map_{layer.id}.geojson"
    out.write_text(json.dumps(gj))
    lons = [c[0] for f in gj["features"] for c in f["geometry"]["coordinates"]]
    lats = [c[1] for f in gj["features"] for c in f["geometry"]["coordinates"]]
    layer.file_uri = str(out)
    layer.bbox = [min(lons), min(lats), max(lons), max(lats)] if lons else []
    z0s = sorted({v for r in parsed["roughness_lines"] for v in (r["z0_left"], r["z0_right"])})
    layer.stats = {
        "title": parsed["title"],
        "roughness_lines": len(parsed["roughness_lines"]),
        "contour_lines": len(parsed["contour_lines"]),
        "z0_values": z0s,
    }
    audit(db, user, "terrain_map_imported", "terrain_layer", layer.id, file=file.filename, crs=code)
    db.commit()
    return layer_to_dict(layer)


class Z0Table(BaseModel):
    table: dict[str, dict]


@router.put("/terrain/{layer_id}/z0-table")
def update_z0(layer_id: int, body: Z0Table, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Table classe → z0 éditable : recalcule la couche de rugosité issue de l'occupation du sol."""
    lc = _layer(db, layer_id, user, ProjectRole.engineer)
    if lc.kind != "landcover":
        raise AppError("TERRAIN_NOT_LANDCOVER", "z0 table applies to a land-cover layer")
    for cls, row in body.table.items():
        z0 = row.get("z0")
        if not isinstance(z0, int | float) or not 0 < z0 <= 5:
            raise AppError("TERRAIN_Z0_INVALID", "z0 must be between 0 and 5 m", cls=cls, value=z0)
    lc.z0_table = body.table
    rough = db.scalar(
        select(TerrainLayer)
        .where(
            TerrainLayer.project_id == lc.project_id,
            TerrainLayer.kind == "roughness",
            TerrainLayer.source == "esa_worldcover",
        )
        .order_by(TerrainLayer.id.desc())
    )
    if rough is None:
        rough = TerrainLayer(
            project_id=lc.project_id,
            kind="roughness",
            name="z0 (ESA WorldCover)",
            source="esa_worldcover",
            file_uri="",
            crs=lc.crs,
            bbox=lc.bbox,
            resolution_m=lc.resolution_m,
        )
        db.add(rough)
        db.flush()
    out = rasters.terrain_dir(lc.project_id) / f"z0_{rough.id}.tif"
    rough.stats = rasters.z0_from_landcover(Path(lc.file_uri), body.table, out)
    rough.file_uri, rough.z0_table = str(out), body.table
    audit(db, user, "terrain_z0_table_updated", "terrain_layer", lc.id)
    db.commit()
    return layer_to_dict(rough)


@router.get("/terrain/{layer_id}/preview.png")
def preview(layer_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    t = _layer(db, layer_id, user)
    if t.kind == "dem":
        png, bounds = rasters.hillshade_png(Path(t.file_uri))
    elif t.kind == "roughness":
        png, bounds = rasters.z0_png(Path(t.file_uri))
    else:
        raise AppError("TERRAIN_NO_PREVIEW", "No image preview for this layer")
    return Response(png, media_type="image/png", headers={"X-Bounds": json.dumps(bounds), "Cache-Control": "no-store"})


@router.get("/terrain/{layer_id}/geojson")
def geojson(layer_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    t = _layer(db, layer_id, user)
    if t.kind != "roughness_map":
        raise AppError("TERRAIN_NO_GEOJSON", "Only WAsP maps have a vector view")
    return Response(Path(t.file_uri).read_bytes(), media_type="application/geo+json")


@router.delete("/terrain/{layer_id}", status_code=204)
def delete_layer(layer_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    t = _layer(db, layer_id, user, ProjectRole.engineer)
    if t.file_uri:
        Path(t.file_uri).unlink(missing_ok=True)
    db.delete(t)
    db.commit()
