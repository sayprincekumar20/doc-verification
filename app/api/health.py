from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.config import get_settings
from app.db.session import get_db
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoAPIError, ZohoAuthError, ZohoError
from app.zoho.factory import get_zoho_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        import redis

        redis.Redis.from_url(get_settings().redis_url, socket_timeout=2).ping()
    except Exception as exc:
        raise HTTPException(503, f"not ready: {type(exc).__name__}") from exc
    return {"status": "ready"}


@router.get("/zoho", dependencies=[Depends(require_api_key)])
def zoho(client: ZohoClient = Depends(get_zoho_client)) -> dict:
    """Confirms the engine can authenticate with Zoho and read as its integration user."""
    try:
        user = client.get_current_user()
    except ZohoAuthError as exc:
        raise HTTPException(503, f"Zoho authentication failed: {exc}") from exc
    except ZohoAPIError as exc:
        raise HTTPException(503, f"Zoho API error {exc.status_code}: {exc.body[:300]}") from exc
    except ZohoError as exc:
        raise HTTPException(503, f"Zoho unavailable: {exc}") from exc
    return {
        "status": "ok",
        "zoho_user": user.get("email"),
        "profile": (user.get("profile") or {}).get("name"),
        "role": (user.get("role") or {}).get("name"),
    }
