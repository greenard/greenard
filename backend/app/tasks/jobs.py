"""Tâches longues. L'état et la progression sont stockés dans `task_log` (lu par l'API)."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

from app.core.errors import AppError
from app.db.models import Site, TaskLog, utcnow
from app.db.session import SessionLocal
from app.nwp import gridpoints, invariants
from app.nwp.catalog import INVARIANTS_FROM, get_model
from app.nwp.invariants import fetch
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


def _progress_writer(task_id: int, lo: float = 0.0, hi: float = 1.0) -> Callable[[float, str], None]:
    def write(p: float, message: str) -> None:
        with SessionLocal() as db:
            t = db.get(TaskLog, task_id)
            if t is not None:
                t.status = "running"
                t.progress = lo + (hi - lo) * max(0.0, min(1.0, p))
                t.message = message
                db.commit()

    return write


def _finish(task_id: int, status: str, message: str = "", result: dict | None = None) -> None:
    with SessionLocal() as db:
        t = db.get(TaskLog, task_id)
        if t is None:
            return
        t.status = status
        t.message = message
        t.result = result or {}
        if status == "success":
            t.progress = 1.0
        t.finished_at = utcnow()
        db.commit()


def _error_payload(exc: Exception) -> dict:
    if isinstance(exc, AppError):
        return {"error": exc.to_dict()}
    return {"error": {"code": "INTERNAL_ERROR", "message": str(exc), "params": {}}}


def gridpoints_domain_blocked(code: str, site_id: int) -> bool:
    with SessionLocal() as db:
        site = db.get(Site, site_id)
        return site is not None and gridpoints.domain_check(code, site.lat, site.lon) is not None


def ensure_invariants(code: str, progress: Callable[[float, str], None]) -> None:
    source = INVARIANTS_FROM.get(code, code)
    if source not in fetch.FETCHERS:
        return  # grille sans champs invariants téléchargeables (ex. IFS 9 km O1280)
    if not invariants.status(source)["ready"]:
        fetch.prepare(source, progress)


@celery_app.task(name="app.tasks.jobs.prepare_invariants")
def prepare_invariants(task_id: int, model_code: str) -> None:
    try:
        meta = fetch.prepare(model_code, _progress_writer(task_id))
        _finish(task_id, "success", "ok", {"model": model_code, "meta": meta})
    except Exception as exc:  # noqa: BLE001 — l'erreur est restituée à l'utilisateur via task_log
        log.error("prepare_invariants failed: %s\n%s", exc, traceback.format_exc())
        _finish(task_id, "failure", str(exc), _error_payload(exc))


@celery_app.task(name="app.tasks.jobs.compute_grid_points")
def compute_grid_points(task_id: int, site_id: int, models: list[str], n: int, method: str) -> None:
    """Prépare au besoin les invariants, puis calcule et enregistre les points de grille du site."""
    warnings: list[dict] = []
    try:
        nm = max(len(models), 1)
        for k, code in enumerate(models):
            get_model(code)
            if gridpoints_domain_blocked(code, site_id):
                continue  # modèle régional hors domaine : inutile de télécharger ses invariants
            try:
                ensure_invariants(code, _progress_writer(task_id, 0.5 * k / nm, 0.5 * (k + 1) / nm))
            except AppError as exc:
                # Source injoignable : on continue avec les autres modèles, l'erreur est remontée.
                warnings.append({"model": code, **exc.to_dict()})
        with SessionLocal() as db:
            site = db.get(Site, site_id)
            if site is None:
                raise AppError("SITE_NOT_FOUND", "Site not found", status_code=404)
            results = gridpoints.compute_for_site(
                db, site, models, n=n, method=method, progress=_progress_writer(task_id, 0.5, 1.0)
            )
            db.commit()
        summary = [
            {
                "model": r.model,
                "status": r.status,
                "message_code": r.message_code,
                "params": r.params,
                "count": len(r.candidates),
            }
            for r in results
        ]
        _finish(task_id, "success", "ok", {"models": summary, "warnings": warnings})
    except Exception as exc:  # noqa: BLE001
        log.error("compute_grid_points failed: %s\n%s", exc, traceback.format_exc())
        _finish(task_id, "failure", str(exc), _error_payload(exc) | {"warnings": warnings})


@celery_app.task(name="app.tasks.jobs.download_forecasts")
def download_forecasts(task_id: int, job_id: int) -> None:
    from app.db.models import ForecastJob
    from app.forecasts.service import run_job

    try:
        with SessionLocal() as db:
            job = db.get(ForecastJob, job_id)
            if job is None:
                raise AppError("FORECAST_JOB_NOT_FOUND", "Job not found", status_code=404)
            job.status = "running"
            db.commit()
            run_job(db, job, _progress_writer(task_id))
            ok = sum(e.status == "success" for e in job.extracts)
            job.status = "success" if ok == len(job.extracts) else ("partial" if ok else "failure")
            db.commit()
            summary = {
                "job_id": job.id,
                "status": job.status,
                "models": [{"model": e.model_code, "status": e.status, "source": e.source} for e in job.extracts],
            }
        _finish(task_id, "success" if ok else "failure", "ok" if ok else "all models failed", summary)
    except Exception as exc:  # noqa: BLE001
        log.error("download_forecasts failed: %s\n%s", exc, traceback.format_exc())
        with SessionLocal() as db:
            from app.db.models import ForecastJob

            job = db.get(ForecastJob, job_id)
            if job is not None:
                job.status = "failure"
                db.commit()
        _finish(task_id, "failure", str(exc), _error_payload(exc))


@celery_app.task(name="app.tasks.jobs.archive_runs")
def archive_runs() -> dict:
    """Archivage automatique : pour chaque projet activé, télécharge les nouveaux runs sur les points
    sélectionnés des sites (le DWD ne conserve que ~24 h ; base de la calibration du jalon 6)."""
    from sqlalchemy import select

    from app.db.models import ForecastExtract, ForecastJob, NwpRun, Project, SiteGridPoint
    from app.forecasts.service import default_params, run_job
    from app.nwp.sources.base import http_client
    from app.nwp.sources.registry import get_source, sources_for

    created = []
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project).where(Project.archive_enabled.is_(True))))
        for project in projects:
            for site in project.sites:
                for model in project.archive_models or []:
                    selected = db.scalar(
                        select(SiteGridPoint).where(
                            SiteGridPoint.site_id == site.id,
                            SiteGridPoint.model_code == model,
                            SiteGridPoint.selected.is_(True),
                        )
                    )
                    if selected is None:
                        continue
                    # run le plus récent disponible sur la source préférée
                    latest = None
                    for code in sources_for(model):
                        try:
                            with http_client() as c:
                                latest = get_source(model, code).latest_run(c)
                            break
                        except Exception:  # noqa: BLE001
                            continue
                    if latest is None:
                        continue
                    done = db.scalar(
                        select(ForecastExtract.id)
                        .join(NwpRun)
                        .join(ForecastJob)
                        .where(
                            ForecastJob.site_id == site.id,
                            NwpRun.model_code == model,
                            NwpRun.init_time == latest,
                            ForecastExtract.status == "success",
                        )
                    )
                    if done:
                        continue
                    params = default_params(
                        models=[model], run=latest.isoformat(), max_lead_h=project.archive_max_lead_h
                    )
                    task = TaskLog(kind="archive_runs", project_id=project.id, message=f"{model} {latest:%Y-%m-%d %HZ}")
                    db.add(task)
                    db.flush()
                    job = ForecastJob(
                        project_id=project.id,
                        site_id=site.id,
                        kind="archive",
                        params=params,
                        status="running",
                        task_id=task.id,
                    )
                    db.add(job)
                    db.commit()
                    try:
                        run_job(db, job, _progress_writer(task.id))
                        job.status = "success" if all(e.status == "success" for e in job.extracts) else "failure"
                    except Exception as exc:  # noqa: BLE001
                        log.error("archive failed: %s", exc)
                        job.status = "failure"
                    db.commit()
                    _finish(
                        task.id, "success" if job.status == "success" else "failure", job.status, {"job_id": job.id}
                    )
                    created.append(job.id)
    return {"jobs": created}
