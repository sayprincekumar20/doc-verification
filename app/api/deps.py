import secrets
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def require_api_key(
    x_api_key: str = Header(default="", alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not secrets.compare_digest(x_api_key, settings.api_key.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")


def get_enqueuer() -> Callable[[str], None]:
    """Returns a function that queues collection for a job id (overridden in tests)."""
    from app.workers.tasks import collect_documents

    return lambda job_id: collect_documents.delay(job_id)
