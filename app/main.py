from fastapi import FastAPI

from app.api import health, jobs, reviews
from app.config import get_settings
from app.logging import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env,
                        send_default_pii=False)

    app = FastAPI(title="Document Verification Engine", version="0.1.0",
                  docs_url="/docs" if settings.app_env != "prod" else None)
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(reviews.router)
    origins = [o.strip() for o in settings.dashboard_origins.split(",") if o.strip()]
    if origins:   # only when the dashboard calls the engine from the browser
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT"],
                           allow_headers=["X-API-Key", "X-Reviewer", "Content-Type"])
    return app


app = create_app()
