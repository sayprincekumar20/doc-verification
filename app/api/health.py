from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db

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
