"""Téléchargement des prévisions : orchestration par modèle, choix de la source, stockage, traçabilité."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import xarray as xr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import ForecastExtract, ForecastJob, GridPoint, NwpRun, SiteGridPoint
from app.forecasts import governance
from app.nwp.catalog import get_model
from app.nwp.sources.base import FetchRequest, PointRef, SourceError, http_client
from app.nwp.sources.registry import get_source, sources_for
from app.nwp.variables import VariableRequest

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


@dataclass
class JobParams:
    models: list[str]
    run: str = "latest"  # "latest" ou horodatage ISO (UTC) d'initialisation — archives
    max_lead_h: int = 240
    wind_heights_m: list[int] = field(default_factory=lambda: [10, 100])
    pressure_levels_hpa: list[int] = field(default_factory=list)
    surface: list[str] = field(default_factory=lambda: ["gust_10m", "t_2m", "rh_2m", "sp", "msl"])
    source: str = "auto"
    members: list[int] | None = None
    accept_paid: bool = False

    def variables(self) -> list[str]:
        return VariableRequest(
            tuple(self.wind_heights_m), tuple(self.pressure_levels_hpa), tuple(self.surface)
        ).canonical()

    def run_time(self) -> datetime | None:
        if self.run == "latest":
            return None
        t = pd.Timestamp(self.run)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        return t.to_pydatetime()


def selected_points(db: Session, site_id: int, model: str) -> list[PointRef]:
    rows = db.execute(
        select(GridPoint)
        .join(SiteGridPoint, SiteGridPoint.grid_point_id == GridPoint.id)
        .where(SiteGridPoint.site_id == site_id, SiteGridPoint.model_code == model, SiteGridPoint.selected.is_(True))
        .order_by(SiteGridPoint.rank)
    ).scalars()
    return [PointRef(g.id, g.native_index, g.lat, g.lon, g.i, g.j) for g in rows]


def storage_path(job: ForecastJob, model: str) -> Path:
    d = get_settings().data_dir / "forecasts" / f"p{job.project_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"job{job.id}_{model}.nc"


def save_dataset(ds: xr.Dataset, path: Path) -> str:
    enc = {v: {"zlib": True, "complevel": 4} for v in ds.data_vars}
    tmp = path.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, encoding=enc)
    tmp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_raw(ext: ForecastExtract) -> xr.Dataset:
    if ext.status != "success" or not ext.file_uri:
        raise AppError("FORECAST_EXTRACT_UNAVAILABLE", "No data for this model", model=ext.model_code, status_code=404)
    with xr.open_dataset(ext.file_uri) as ds:
        return ds.load()


def _upsert_run(db: Session, model: str, init: datetime, source: str) -> NwpRun:
    run = db.scalar(select(NwpRun).where(NwpRun.model_code == model, NwpRun.init_time == init, NwpRun.source == source))
    if run is None:
        run = NwpRun(model_code=model, init_time=init, source=source)
        db.add(run)
        db.flush()
    return run


def estimate(db: Session, site_id: int, params: JobParams) -> list[dict]:
    """Estimation (volume, échéances, variables) par modèle et par source candidate, sans téléchargement."""
    out = []
    with http_client() as client:
        for model in params.models:
            get_model(model)
            pts = selected_points(db, site_id, model)
            item: dict = {"model": model, "points": len(pts), "candidates": []}
            if not pts:
                item["error"] = {"code": "FORECAST_NO_POINT_SELECTED", "params": {"model": model}}
                out.append(item)
                continue
            codes = sources_for(model) if params.source == "auto" else [params.source]
            for code in codes:
                cand: dict = {"source": code}
                try:
                    eff = governance.check_allowed(db, code, params.accept_paid)
                    cand["licence"], cand["cost"] = eff.usage_licence, eff.cost
                    src = get_source(model, code)
                    run = params.run_time() or src.latest_run(client)
                    req = FetchRequest(model, run, params.max_lead_h, pts, params.variables(), params.members)
                    cand["run"] = run.isoformat()
                    est = src.estimate(client, req)
                    cand["estimate"] = est.to_dict()
                    cand["over_limit"] = est.bytes is not None and est.bytes > get_settings().max_download_mb * 1e6
                except AppError as exc:
                    cand["error"] = exc.to_dict()
                item["candidates"].append(cand)
            out.append(item)
    return out


def run_job(db: Session, job: ForecastJob, progress: Progress) -> None:
    params = JobParams(**job.params)
    n = max(len(params.models), 1)
    for k, model in enumerate(params.models):
        lo, hi = k / n, (k + 1) / n

        def sub(p: float, msg: str, lo=lo, hi=hi, model=model) -> None:
            progress(lo + (hi - lo) * p, f"{model}: {msg}")

        ext = ForecastExtract(job_id=job.id, model_code=model, status="running")
        db.add(ext)
        db.commit()
        pts = selected_points(db, job.site_id, model)
        ext.grid_point_ids = [p.grid_point_id for p in pts]
        if not pts:
            ext.status = "failure"
            ext.error = {
                "code": "FORECAST_NO_POINT_SELECTED",
                "message": "No grid point selected",
                "params": {"model": model},
            }
            db.commit()
            continue
        codes = sources_for(model) if params.source == "auto" else [params.source]
        attempts = []
        for code in codes:
            try:
                eff = governance.check_allowed(db, code, params.accept_paid)
                src = get_source(model, code)
                with http_client() as client:
                    run = params.run_time() or src.latest_run(client)
                    req = FetchRequest(model, run, params.max_lead_h, pts, params.variables(), params.members)
                    est = src.estimate(client, req)
                    ext.estimate = est.to_dict()
                    sub(0.02, f"{code} {run:%Y-%m-%d %HZ}")
                    ds = src.fetch(client, req, sub)
            except (AppError, SourceError) as exc:
                attempts.append({"source": code, **exc.to_dict()})
                log.warning("source %s failed for %s: %s", code, model, exc)
                continue
            except Exception as exc:  # noqa: BLE001 — erreur inattendue : on tente la source suivante
                log.exception("unexpected error with source %s", code)
                attempts.append({"source": code, "code": "INTERNAL_ERROR", "message": str(exc), "params": {}})
                continue
            if not ds.data_vars:
                attempts.append(
                    {"source": code, "code": "SOURCE_NO_DATA", "message": "No variable returned", "params": {}}
                )
                continue
            ds.attrs.update(
                {
                    "site_id": job.site_id,
                    "job_id": job.id,
                    "licence_class": eff.usage_licence,
                    "cost": eff.cost,
                    "app": "greenard",
                }
            )
            path = storage_path(job, model)
            ext.file_hash = save_dataset(ds, path)
            ext.file_uri = str(path)
            ext.source = code
            ext.paid = eff.cost == "paid"
            ext.run = _upsert_run(db, model, run, code)
            ext.n_members = int(ds.sizes["member"])
            ext.n_times = int(ds.sizes["time"])
            ext.variables = sorted(ds.data_vars)
            ext.missing_variables = [v for v in ds.attrs.get("missing_variables", "").split(",") if v]
            ext.status = "success"
            break
        else:
            ext.status = "failure"
            ext.error = {
                "code": "FORECAST_ALL_SOURCES_FAILED",
                "message": "All sources failed",
                "params": {"model": model},
            }
        ext.attempts = attempts
        db.commit()
    progress(1.0, "done")


def job_to_dict(job: ForecastJob) -> dict:
    return {
        "id": job.id,
        "site_id": job.site_id,
        "kind": job.kind,
        "status": job.status,
        "params": job.params,
        "task_id": job.task_id,
        "created_at": job.created_at,
        "extracts": [
            {
                "id": e.id,
                "model": e.model_code,
                "status": e.status,
                "source": e.source,
                "paid": e.paid,
                "run": e.run.init_time if e.run else None,
                "n_members": e.n_members,
                "n_times": e.n_times,
                "variables": e.variables,
                "missing_variables": e.missing_variables,
                "grid_point_ids": e.grid_point_ids,
                "estimate": e.estimate,
                "error": e.error,
                "attempts": e.attempts,
            }
            for e in job.extracts
        ],
    }


def default_params(**kw) -> dict:
    return asdict(JobParams(**kw))
