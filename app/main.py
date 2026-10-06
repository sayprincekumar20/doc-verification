from fastapi import FastAPI

from app.api import health, jobs
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
    return app


app = create_app()
