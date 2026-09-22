from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, farm, forecasts, geo, mast, models, projects, sites, tasks, terrain, users
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Greenard API", version="0.1.0", openapi_url=f"{API_PREFIX}/openapi.json", docs_url=f"{API_PREFIX}/docs"
    )
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    for r in (auth, users, projects, sites, geo, models, tasks, forecasts, farm, mast, terrain):
        app.include_router(r.router, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok", "deployment_usage": settings.deployment_usage}

    return app


app = create_app()
