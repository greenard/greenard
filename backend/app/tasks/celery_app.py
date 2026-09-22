from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("greenard", broker=settings.redis_url, backend=settings.redis_url, include=["app.tasks.jobs"])
celery_app.conf.update(
    task_always_eager=settings.celery_eager,
    task_eager_propagates=False,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.tasks.jobs.prepare_invariants": {"queue": "io"},
        "app.tasks.jobs.compute_grid_points": {"queue": "io"},
    },
)
