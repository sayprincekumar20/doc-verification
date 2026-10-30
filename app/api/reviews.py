"""Review API for the Accounts dashboard (ENGINE_REVIEWS=true).

Every call needs the X-API-Key header; actions that change a review also need X-Reviewer
(the name or email of the person logged in to the dashboard), which is stored with the
decision. Call it from the dashboard's server side (Cloudflare Worker / Pages Function) so the
API key never reaches the browser."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_api_key
from app.config import Settings, get_settings
from app.db.models import Review
from app.db.session import get_db
from app.notify.events import notify_engine_review
from app.notify.notifier import Notifier
from app.review import engine
from app.storage.base import build_storage
from app.zoho.factory import get_zoho_client

router = APIRouter(prefix="/v1/reviews", tags=["reviews"],
                   dependencies=[Depends(require_api_key)])


def _review(review_id: uuid.UUID, db: Session) -> Review:
    r = db.get(Review, review_id)
    if r is None:
        raise HTTPException(404, "Review not found")
    return r


def _reviewer(x_reviewer: str | None) -> str:
    if not (x_reviewer or "").strip():
        raise HTTPException(422, "Header X-Reviewer (who is acting) is required")
    return x_reviewer.strip()[:255]


def _guard(fn):
    try:
        return fn()
    except engine.ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def list_reviews(status: str | None = Query(None, description="e.g. Pending Review, or 'open'"),
                 account_id: str | None = None, limit: int = Query(50, le=200),
                 offset: int = 0, db: Session = Depends(get_db)) -> dict:
    q = select(Review)
    if status == "open":
        q = q.where(Review.status.in_(engine.OPEN))
    elif status:
        q = q.where(Review.status == status)
    if account_id:
        q = q.where(Review.account_id == account_id)
    rows = db.scalars(q.order_by(Review.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [engine.summary_view(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/{review_id}")
def get_review(review_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return engine.detail_view(_review(review_id, db))


@router.get("/{review_id}/pages/{n}", response_class=Response)
def get_page(review_id: uuid.UUID, n: int, db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)) -> Response:
    r = _review(review_id, db)
    page = next((p for p in r.pages if p["n"] == n), None)
    if page is None or r.images_purged:
        raise HTTPException(404, "Page image not available")
    data = build_storage(settings).get(page["key"])
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/{review_id}/start")
def start(review_id: uuid.UUID, x_reviewer: str | None = Header(None),
          db: Session = Depends(get_db)) -> dict:
    r = _review(review_id, db)
    _guard(lambda: engine.start_review(r, _reviewer(x_reviewer)))
    db.commit()
    return engine.detail_view(r)


class RowDecision(BaseModel):
    decision: str = Field(description="Pending | Approve | Edit | Reject")
    value: str | None = Field(None, description="corrected value, required for Edit")
    comment: str | None = None


@router.put("/{review_id}/rows/{row_id}")
def decide_row(review_id: uuid.UUID, row_id: str, body: RowDecision,
               x_reviewer: str | None = Header(None), db: Session = Depends(get_db)) -> dict:
    r = _review(review_id, db)
    row = _guard(lambda: engine.set_row(r, row_id, body.decision, body.value, body.comment,
                                        _reviewer(x_reviewer)))
    db.commit()
    return row


class Approval(BaseModel):
    values_compared: bool = Field(description="'I compared every approved value with the "
                                              "source document' - must be true")
    activate_account: bool = False
    notes: str | None = None


@router.post("/{review_id}/approve")
def approve(review_id: uuid.UUID, body: Approval, x_reviewer: str | None = Header(None),
            db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    r = _review(review_id, db)
    who = _reviewer(x_reviewer)
    result = _guard(lambda: engine.approve(
        db, r, get_zoho_client(), who, values_compared=body.values_compared,
        activate_account=body.activate_account, notes=body.notes,
        triggers=settings.zoho_update_triggers))
    db.commit()
    _announce(db, r, settings)
    return {**result, "review": engine.detail_view(r)}


class Closing(BaseModel):
    notes: str = Field(description="reason / documents needed (required)")


@router.post("/{review_id}/reject")
def reject(review_id: uuid.UUID, body: Closing, x_reviewer: str | None = Header(None),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    r = _review(review_id, db)
    _guard(lambda: engine.close_without_writing(db, r, _reviewer(x_reviewer), "Rejected",
                                                body.notes))
    db.commit()
    _announce(db, r, settings)
    return engine.detail_view(r)


@router.post("/{review_id}/request-documents")
def request_documents(review_id: uuid.UUID, body: Closing,
                      x_reviewer: str | None = Header(None), db: Session = Depends(get_db),
                      settings: Settings = Depends(get_settings)) -> dict:
    r = _review(review_id, db)
    _guard(lambda: engine.close_without_writing(db, r, _reviewer(x_reviewer),
                                                "Waiting for Documents", body.notes))
    db.commit()
    _announce(db, r, settings)
    return engine.detail_view(r)


def _announce(db: Session, review: Review, settings: Settings) -> None:
    try:
        notify_engine_review(db, review, Notifier.from_settings(settings))
        db.commit()
    except Exception:  # noqa: BLE001 - the decision is saved; a message must not undo it
        db.rollback()
