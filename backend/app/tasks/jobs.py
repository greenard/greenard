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
