"""Mâts de mesure : création, import des séries (aperçu → correspondance → import), contrôle qualité,
analyses (cisaillement, turbulence, rose, densité)."""

import hashlib
import json
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, UploadFile
from geoalchemy2.elements import WKTElement
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import audit, current_user, require_project
from app.api.routes.farm import _read
from app.api.routes.geo import resolve_input
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import MastDataset, MastSensor, MetMast, ProjectRole, TerrainLayer, User
from app.db.session import get_db
from app.mast import analysis, qc
from app.mast.formats import read_file, suggest_mapping
from app.mast.ingest import SensorDef, ingest
from app.terrain import dem

router = APIRouter(tags=["mast"])


def mast_dir(project_id: int, mast_id: int) -> Path:
    d = get_settings().data_dir / "mast" / f"p{project_id}" / f"m{mast_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def upload_dir() -> Path:
    d = get_settings().data_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mast(db: Session, mast_id: int, user: User, minimum: ProjectRole) -> MetMast:
    m = db.scalar(
        select(MetMast)
        .where(MetMast.id == mast_id)
        .options(selectinload(MetMast.sensors), selectinload(MetMast.datasets))
    )
    if m is None:
        raise NotFound("MAST_NOT_FOUND", "Met mast not found")
    require_project(db, m.project_id, user, minimum)
    return m


def mast_to_dict(m: MetMast) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "lat": m.lat,
        "lon": m.lon,
        "input_crs": m.input_crs,
        "input_x": m.input_x,
        "input_y": m.input_y,
        "elevation_m": m.elevation_m,
        "sensors": [
            {
                "id": s.id,
                "code": s.code,
                "kind": s.kind,
                "height_m": s.height_m,
                "boom_dir_deg": s.boom_dir_deg,
                "unit": s.unit,
            }
            for s in m.sensors
        ],
        "datasets": [
            {
                "id": d.id,
                "original_filename": d.original_filename,
                "format": d.format,
                "t_start": d.t_start,
                "t_end": d.t_end,
                "n_records": d.n_records,
                "created_at": d.created_at,
                "report": d.qc_summary.get("report", {}),
                "valid_pct": {s["code"]: s["valid_pct"] for s in d.qc_summary.get("sensors", [])},
            }
            for d in sorted(m.datasets, key=lambda d: d.id)
        ],
    }


class MastIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    lat: str | float | None = None
    lon: str | float | None = None
    x: float | None = None
    y: float | None = None
    crs: str = "EPSG:4326"
    elevation_m: float | None = None


@router.get("/projects/{project_id}/masts")
def list_masts(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    require_project(db, project_id, user, ProjectRole.viewer)
    rows = db.scalars(
        select(MetMast)
        .where(MetMast.project_id == project_id)
        .options(selectinload(MetMast.sensors), selectinload(MetMast.datasets))
        .order_by(MetMast.name)
    )
    return [mast_to_dict(m) for m in rows]


@router.post("/projects/{project_id}/masts", status_code=201)
def create_mast(
    project_id: int, body: MastIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    require_project(db, project_id, user, ProjectRole.engineer)
    lat, lon, info = resolve_input(body.lat, body.lon, body.x, body.y, body.crs)
    elev = body.elevation_m
    if elev is None:
        try:
            s = dem.sample(round(lat, 6), round(lon, 6))
            elev = None if s.sea_tile else s.elevation_m
        except Exception:  # noqa: BLE001
            elev = None
    m = MetMast(
        project_id=project_id,
        name=body.name,
        lat=lat,
        lon=lon,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
        input_crs=info["crs"],
        input_x=body.x,
        input_y=body.y,
        elevation_m=elev,
    )
    db.add(m)
    db.flush()
    audit(db, user, "mast_created", "met_mast", m.id, name=m.name)
    db.commit()
    return mast_to_dict(_mast(db, m.id, user, ProjectRole.viewer))


@router.delete("/masts/{mast_id}", status_code=204)
def delete_mast(mast_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    m = _mast(db, mast_id, user, ProjectRole.engineer)
    for d in m.datasets:
        Path(d.file_uri).unlink(missing_ok=True)
    audit(db, user, "mast_deleted", "met_mast", m.id, name=m.name)
    db.delete(m)
    db.commit()


# ---- import des séries -----------------------------------------------------------------------------


@router.post("/masts/{mast_id}/data/preview")
async def preview_data(
    mast_id: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """Détecte le format, propose une correspondance colonnes → capteurs ; le fichier est conservé
    temporairement (jeton) pour l'import."""
    _mast(db, mast_id, user, ProjectRole.engineer)
    data = await _read(file)
    raw = read_file(file.filename or "", data)
    token = uuid.uuid4().hex
    (upload_dir() / f"{token}.bin").write_bytes(data)
    (upload_dir() / f"{token}.json").write_text(json.dumps({"filename": file.filename, "mast_id": mast_id}))
    sample = raw.frame.head(8).fillna("").astype(str)
    return {
        "token": token,
        "format": raw.format,
        "time_column": raw.time_column,
        "default_convention": raw.default_convention,
        "columns": list(raw.frame.columns),
        "units": raw.units,
        "header": raw.header,
        "n_rows": int(len(raw.frame)),
        "sample": sample.to_dict("records"),
        "suggested_mapping": suggest_mapping(list(raw.frame.columns), raw.time_column),
    }


class ImportIn(BaseModel):
    token: str = Field(pattern="^[0-9a-f]{32}$")
    mapping: list[dict]
    timezone: str = "UTC"
    convention: str = Field("end", pattern="^(start|end)$")
    time_format: str | None = None
    dayfirst: bool = False


@router.post("/masts/{mast_id}/data/import", status_code=201)
def import_data(
    mast_id: int, body: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    m = _mast(db, mast_id, user, ProjectRole.engineer)
    path = upload_dir() / f"{body.token}.bin"
    meta_path = upload_dir() / f"{body.token}.json"
    if not path.exists():
        raise AppError("MAST_UPLOAD_EXPIRED", "Uploaded file not found: preview it again", status_code=404)
    meta = json.loads(meta_path.read_text())
    if meta.get("mast_id") != mast_id:
        raise AppError("MAST_UPLOAD_EXPIRED", "Upload belongs to another mast", status_code=404)
    data = path.read_bytes()
    raw = read_file(meta["filename"], data)
    time_ref = {
        "timezone": body.timezone,
        "convention": body.convention,
        "format": body.time_format,
        "dayfirst": body.dayfirst,
    }
    ing = ingest(raw, body.mapping, time_ref)
    checked = qc.run_qc(ing.frame, ing.sensors)
    # capteurs : création ou mise à jour (hauteur, orientation)
    existing = {s.code: s for s in m.sensors}
    for s in ing.sensors:
        row = existing.get(s.code)
        if row is None:
            db.add(
                MastSensor(
                    mast_id=m.id,
                    code=s.code,
                    kind=s.kind,
                    height_m=s.height_m,
                    boom_dir_deg=s.boom_dir_deg,
                    unit=s.unit,
                )
            )
        else:
            row.height_m, row.boom_dir_deg = s.height_m, s.boom_dir_deg
    ds = MastDataset(
        mast_id=m.id,
        original_filename=meta["filename"],
        format=raw.format,
        mapping={"columns": body.mapping},
        time_reference=time_ref,
        file_uri="",
        file_hash=hashlib.sha256(data).hexdigest(),
        t_start=ing.frame.index[0].tz_localize("UTC"),
        t_end=ing.frame.index[-1].tz_localize("UTC"),
        n_records=int(len(ing.frame)),
        created_by=user.id,
    )
    db.add(ds)
    db.flush()
    out = mast_dir(m.project_id, m.id) / f"ds{ds.id}.parquet"
    checked.to_parquet(out)
    ds.file_uri = str(out)
    summ = qc.summary(checked, ing.sensors)
    summ["report"] = ing.report
    summ["sensor_defs"] = [s.__dict__ for s in ing.sensors]
    ds.qc_summary = summ
    audit(db, user, "mast_data_imported", "met_mast", m.id, dataset=ds.id, file=meta["filename"], records=ds.n_records)
    db.commit()
    path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    return {"dataset_id": ds.id, "report": ing.report, "qc": {k: v for k, v in summ.items() if k != "sensor_defs"}}


def _dataset(db: Session, dataset_id: int, user: User, minimum=ProjectRole.viewer) -> tuple[MastDataset, MetMast]:
    ds = db.get(MastDataset, dataset_id)
    if ds is None:
        raise NotFound("MAST_DATASET_NOT_FOUND", "Dataset not found")
    return ds, _mast(db, ds.mast_id, user, minimum)


def _frame(ds: MastDataset) -> pd.DataFrame:
    return pd.read_parquet(ds.file_uri)


def _sensors(ds: MastDataset) -> list[SensorDef]:
    return [SensorDef(**s) for s in ds.qc_summary.get("sensor_defs", [])]


@router.delete("/mast-datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    ds, _ = _dataset(db, dataset_id, user, ProjectRole.engineer)
    Path(ds.file_uri).unlink(missing_ok=True)
    db.delete(ds)
    db.commit()


@router.get("/mast-datasets/{dataset_id}/qc")
def dataset_qc(dataset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    ds, _ = _dataset(db, dataset_id, user)
    return {k: v for k, v in ds.qc_summary.items() if k != "sensor_defs"} | {"flag_names": qc.FLAG_NAMES}


def _clean(a) -> list:
    return [None if not np.isfinite(x) else round(float(x), 3) for x in np.asarray(a, dtype=float)]


@router.get("/mast-datasets/{dataset_id}/series")
def dataset_series(
    dataset_id: int,
    start: str | None = None,
    end: str | None = None,
    resample: str = "auto",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Séries brutes par capteur (valeur + drapeaux) et composites valides ; ré-échantillonnage
    horaire automatique au-delà de 31 jours."""
    ds, _ = _dataset(db, dataset_id, user)
    df = _frame(ds)
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        return {"times": [], "sensors": {}, "composites": {}, "resample": None}
    span_days = (df.index[-1] - df.index[0]).days
    rule = (
        None
        if resample == "10min" or (resample == "auto" and span_days <= 31)
        else ("1D" if resample == "1d" else "1h")
    )
    sensors = _sensors(ds)
    out = {"sensors": {}, "composites": {}, "resample": rule or "10min"}
    if rule is None:
        out["times"] = df.index.strftime("%Y-%m-%dT%H:%M:%SZ").tolist()
        for s in sensors:
            out["sensors"][s.code] = {
                "kind": s.kind,
                "height_m": s.height_m,
                "values": _clean(df[f"{s.code}__mean"]),
                "flags": df[f"{s.code}__flag"].astype(int).tolist(),
            }
        comp = df
    else:
        comp = df[[c for c in df.columns if "@" in c]].resample(rule, label="right", closed="right").mean()
        out["times"] = comp.index.strftime("%Y-%m-%dT%H:%M:%SZ").tolist()
        for s in sensors:
            flagged = (df[f"{s.code}__flag"] > 0).resample(rule, label="right", closed="right").mean() * 100
            out["sensors"][s.code] = {"kind": s.kind, "height_m": s.height_m, "flagged_pct": _clean(flagged)}
    for c in [c for c in comp.columns if "@" in c]:
        if c.startswith("wd@") and rule is not None:
            continue  # la moyenne arithmétique de directions n'a pas de sens
        out["composites"][c] = _clean(comp[c])
    return out


@router.get("/mast-datasets/{dataset_id}/analysis")
def dataset_analysis(
    dataset_id: int, sectors: int = 12, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    ds, m = _dataset(db, dataset_id, user)
    df = _frame(ds)
    z0 = None
    layer = db.scalar(
        select(TerrainLayer)
        .where(TerrainLayer.project_id == m.project_id, TerrainLayer.kind == "roughness")
        .order_by(TerrainLayer.id.desc())
    )
    if layer is not None:
        from app.terrain.rasters import z0_by_sector

        try:
            z0 = z0_by_sector(Path(layer.file_uri), m.lat, m.lon, sectors)
        except Exception:  # noqa: BLE001 — mât hors de l'emprise de la couche
            z0 = None
    return {
        "overview": analysis.overview(df),
        "shear": analysis.shear(df, sectors),
        "turbulence": analysis.turbulence(df, sectors=sectors),
        "wind_rose": analysis.wind_rose(df, 16),
        "air_density": analysis.air_density(df),
        "z0_by_sector": z0,
    }
