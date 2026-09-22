from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import admin_user, audit, current_user, require_project
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import ForecastExtract, ForecastJob, ProjectRole, Site, TaskLog, User
from app.db.session import get_db
from app.forecasts import analysis, export
from app.forecasts.service import JobParams, estimate, job_to_dict, load_raw
from app.nwp.catalog import get_model
from app.nwp.timeseries import STEPS, harmonise
from app.tasks import jobs

router = APIRouter(tags=["forecasts"])

SURFACE_ALLOWED = {"gust_10m", "t_2m", "rh_2m", "sp", "msl"}


class ForecastRequest(BaseModel):
    models: list[str] = Field(min_length=1)
    run: str = "latest"
    max_lead_h: int = Field(240, ge=1, le=384)
    wind_heights_m: list[int] = Field(default_factory=lambda: [10, 100])
    pressure_levels_hpa: list[int] = Field(default_factory=list)
    surface: list[str] = Field(default_factory=lambda: ["gust_10m", "t_2m", "rh_2m", "sp", "msl"])
    source: str = "auto"
    members: list[int] | None = None
    accept_paid: bool = False

    def to_params(self) -> JobParams:
        for m in self.models:
            get_model(m)
        bad = set(self.surface) - SURFACE_ALLOWED
        if bad:
            raise AppError("FORECAST_VARIABLE_UNKNOWN", "Unknown variable", variables=sorted(bad))
        if any(h < 2 or h > 300 for h in self.wind_heights_m):
            raise AppError("FORECAST_HEIGHT_INVALID", "Wind heights must be between 2 and 300 m")
        if self.run != "latest":
            try:
                t = datetime.fromisoformat(self.run.replace("Z", "+00:00"))
            except ValueError as exc:
                raise AppError("FORECAST_RUN_INVALID", "Invalid run time", value=self.run) from exc
            if t.tzinfo is not None and t > datetime.now(UTC):
                raise AppError("FORECAST_RUN_INVALID", "Run time is in the future", value=self.run)
        return JobParams(**self.model_dump())


def _site(db: Session, site_id: int, user: User, minimum: ProjectRole) -> Site:
    site = db.get(Site, site_id)
    if site is None:
        raise NotFound("SITE_NOT_FOUND", "Site not found")
    require_project(db, site.project_id, user, minimum)
    return site


def _job(db: Session, job_id: int, user: User) -> ForecastJob:
    job = db.scalar(select(ForecastJob).where(ForecastJob.id == job_id).options(selectinload(ForecastJob.extracts)))
    if job is None:
        raise NotFound("FORECAST_JOB_NOT_FOUND", "Forecast job not found")
    require_project(db, job.project_id, user, ProjectRole.viewer)
    return job


@router.post("/sites/{site_id}/forecasts/estimate")
def estimate_forecast(
    site_id: int, body: ForecastRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    _site(db, site_id, user, ProjectRole.viewer)
    return {"limit_mb": get_settings().max_download_mb, "models": estimate(db, site_id, body.to_params())}


@router.post("/sites/{site_id}/forecasts", status_code=202)
def create_forecast(
    site_id: int, body: ForecastRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    site = _site(db, site_id, user, ProjectRole.engineer)
    params = body.to_params()
    task = TaskLog(
        kind="download_forecasts", created_by=user.id, project_id=site.project_id, message=",".join(params.models)
    )
    db.add(task)
    db.flush()
    job = ForecastJob(
        project_id=site.project_id, site_id=site.id, params=body.model_dump(), task_id=task.id, created_by=user.id
    )
    db.add(job)
    audit(db, user, "forecast_requested", "site", site.id, **body.model_dump())
    db.commit()
    res = jobs.download_forecasts.delay(task.id, job.id)
    task.celery_id = res.id
    db.commit()
    db.refresh(job)
    return job_to_dict(job)


@router.get("/sites/{site_id}/forecasts")
def list_forecasts(
    site_id: int, limit: int = 30, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    _site(db, site_id, user, ProjectRole.viewer)
    rows = db.scalars(
        select(ForecastJob)
        .where(ForecastJob.site_id == site_id)
        .options(selectinload(ForecastJob.extracts))
        .order_by(ForecastJob.created_at.desc())
        .limit(min(limit, 200))
    )
    return [job_to_dict(j) for j in rows]


@router.get("/forecasts/{job_id}")
def get_forecast(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return job_to_dict(_job(db, job_id, user))


@router.delete("/forecasts/{job_id}", status_code=204)
def delete_forecast(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    job = _job(db, job_id, user)
    require_project(db, job.project_id, user, ProjectRole.engineer)
    for e in job.extracts:
        if e.file_uri:
            Path(e.file_uri).unlink(missing_ok=True)
    audit(db, user, "forecast_deleted", "forecast_job", job.id)
    db.delete(job)
    db.commit()


def _loaded(job: ForecastJob, models: list[str] | None = None) -> list[tuple[ForecastExtract, object]]:
    out = []
    for e in job.extracts:
        if e.status == "success" and (not models or e.model_code in models):
            out.append((e, load_raw(e)))
    if not out:
        raise AppError("FORECAST_NO_DATA", "No successful model in this job", status_code=404)
    return out


def _check_step(step: str, method: str, speed_method: str) -> None:
    if step not in STEPS or method not in ("linear", "pchip") or speed_method not in ("scalar", "vector"):
        raise AppError("FORECAST_HARMONISATION_INVALID", "Invalid step / method", step=step)


@router.get("/forecasts/{job_id}/series")
def forecast_series(
    job_id: int,
    step: str = "1h",
    method: str = "linear",
    speed_method: str = "scalar",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _check_step(step, method, speed_method)
    job = _job(db, job_id, user)
    return {
        "step": step,
        "method": method,
        "speed_method": speed_method,
        "models": [
            analysis.series_payload(harmonise(raw, step, method, speed_method), e.model_code) for e, raw in _loaded(job)
        ],
    }


@router.get("/forecasts/{job_id}/windrose")
def forecast_windrose(
    job_id: int,
    height: int = 100,
    sectors: int = Query(12, ge=4, le=36),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    job = _job(db, job_id, user)
    return {
        "height_m": height,
        "models": {e.model_code: analysis.wind_rose(raw, height, sectors) for e, raw in _loaded(job)},
    }


@router.get("/forecasts/{job_id}/comparison")
def forecast_comparison(
    job_id: int,
    height: int = 100,
    step: str = "3h",
    method: str = "linear",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _check_step(step, method, "scalar")
    job = _job(db, job_id, user)
    return analysis.comparison({e.model_code: raw for e, raw in _loaded(job)}, height, step, method)


@router.get("/forecasts/{job_id}/export")
def forecast_export(
    job_id: int,
    fmt: str = "csv",
    step: str = "1h",
    method: str = "linear",
    speed_method: str = "scalar",
    tz: str = "UTC",
    models: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    _check_step(step, method, speed_method)
    if fmt not in ("csv", "xlsx", "nc"):
        raise AppError("EXPORT_FORMAT_INVALID", "Format must be csv, xlsx or nc", fmt=fmt)
    tzv = "Africa/Casablanca" if tz == "Africa/Casablanca" else None
    job = _job(db, job_id, user)
    site = db.get(Site, job.site_id)
    selected = [m for m in models.split(",") if m] or None
    extracts = [(e.model_code, harmonise(raw, step, method, speed_method)) for e, raw in _loaded(job, selected)]
    meta = export.metadata(job, site, extracts, step, method, speed_method, tzv)
    name = f"greenard_job{job.id}_{site.name.replace(' ', '_')}_{step}"
    audit(db, user, "forecast_exported", "forecast_job", job.id, fmt=fmt, step=step)
    db.commit()
    if fmt == "nc":
        return Response(
            export.to_netcdf(meta, extracts),
            media_type="application/x-netcdf",
            headers={"Content-Disposition": f'attachment; filename="{name}.nc"'},
        )
    df, units = export.build_frame(extracts, tzv)
    if fmt == "xlsx":
        if len(df) > 1_000_000:
            raise AppError("EXPORT_TOO_MANY_ROWS", "Too many rows for XLSX; use CSV or NetCDF", rows=len(df))
        return Response(
            export.to_xlsx(meta, df, units),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    return Response(
        export.to_csv(meta, df, units),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


# ---- sources de données -----------------------------------------------------------------------


class DataSourceUpdate(BaseModel):
    enabled: bool


@router.get("/data-sources")
def list_data_sources(_: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.forecasts.governance import list_sources

    s = get_settings()
    return {
        "deployment_usage": s.deployment_usage,
        "open_meteo_mode": s.open_meteo_mode,
        "max_download_mb": s.max_download_mb,
        "sources": list_sources(db),
    }


@router.patch("/data-sources/{code}")
def update_data_source(
    code: str, body: DataSourceUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    from app.db.models import DataSource
    from app.forecasts.governance import effective

    admin_user(user)
    row = db.get(DataSource, code)
    if row is None:
        raise NotFound("SOURCE_UNKNOWN", "Unknown data source", source=code)
    if body.enabled and not row.implemented:
        raise AppError("SOURCE_NOT_IMPLEMENTED", "This data source is not implemented", source=code)
    row.enabled = body.enabled
    audit(db, user, "data_source_updated", "data_source", code, enabled=body.enabled, cost=row.cost)
    db.commit()
    return effective(db, code).__dict__
