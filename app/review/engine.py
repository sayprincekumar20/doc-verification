"""Reviews in the Accounts dashboard (ENGINE_REVIEWS=true): created after each verification,
decided through the review API, written back to the Zoho Account with the same safety rules
as the Zoho review module (app/review/apply.py: apply_rows)."""

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Document,
    DocumentStatus,
    FilePage,
    JobAssessment,
    Review,
    VerificationJob,
    utcnow,
)
from app.pipeline.read import PIPELINE_VERSION
from app.review.apply import apply_rows
from app.services.audit import record_event
from app.storage.base import Storage
from app.zoho.client import ZohoClient
from app.zoho.review_record import DEFAULT_NAMES, build_record, short_name

log = logging.getLogger(__name__)

OPEN = ("Pending Review", "In Review")
CLOSED = ("Applied", "Conflict", "Failed", "Rejected", "Waiting for Documents", "No Action")
DECISIONS = ("Pending", "Approve", "Edit", "Reject")


class ReviewError(ValueError):
    """A reviewer action that is not allowed (shown to the dashboard as HTTP 409/422)."""


def create_engine_review(db: Session, job: VerificationJob, storage: Storage,
                         max_images: int = 10) -> Review | None:
    row = db.get(JobAssessment, job.id)
    if row is None:
        return None
    existing = db.scalar(select(Review).where(Review.job_id == job.id))
    if existing:
        return existing
    f, sf = DEFAULT_NAMES["fields"], DEFAULT_NAMES["subform_fields"]
    rec = build_record(row.assessment, job.account_id, job_id=str(job.id),
                       requested_by=job.requested_by_email, request_reason=job.reason,
                       names=DEFAULT_NAMES)
    rows = [{"id": str(i), "zoho_field": r.get(sf["Zoho Field"]),
             "field_label": r.get(sf["Field Label"]), "current_value": r.get(sf["Current Value"]),
             "proposed_value": r.get(sf["Proposed Value"]), "action": r.get(sf["Action"]),
             "confidence": r.get(sf["Confidence"]), "ocr_check": r.get(sf["OCR Check"]),
             "evidence": r.get(sf["Evidence"]), "auto_applied": r.get(sf["Auto Applied"]),
             "decision": r.get(sf["Decision"]) or "Pending", "edited_value": None,
             "comment": None, "outcome": None, "outcome_detail": None}
            for i, r in enumerate(rec.get(f["Proposed Changes"]) or [], 1)]
    status = rec.get(f["Review Status"]) or "Pending Review"
    snap = job.account_snapshot or {}
    review = Review(
        number=(db.scalar(select(func.max(Review.number))) or 0) + 1, job_id=job.id,
        account_id=job.account_id, account_name=snap.get("Account_Name"),
        customer_number=snap.get("Inventory_Customer_Number") or job.customer_number,
        status="No Action" if status == "Applied" else status,
        recommendation=row.assessment["recommendation"],
        summary={"alerts": rec.get(f["Alerts"]),
                 "recommendation_reasons": rec.get(f["Recommendation Reasons"]),
                 "required_documents": rec.get(f["Required Documents"]),
                 "documents_found": rec.get(f["Documents Found"]),
                 "lowest_confidence": rec.get(f["Lowest Confidence"]),
                 "activation_checks": row.assessment.get("activation_checks") or [],
                 "rules_version": row.rules_version, "request_reason": job.reason},
        rows=rows, pages=[], requested_by=job.requested_by_email)
    if review.status in CLOSED:
        review.closed_at = utcnow()
    db.add(review)
    db.flush()
    review.pages = _copy_pages(db, job, storage, review, max_images)
    record_event(db, "ENGINE_REVIEW_CREATED", f"DV-{review.number}: {review.status}", job.id,
                 {"review_id": str(review.id), "pages": len(review.pages)})
    db.commit()
    return review


def _copy_pages(db, job, storage, review, max_images) -> list[dict]:
    """Copies of the cleaned page images, kept for the reviewer (the job's own copies are
    deleted after the job)."""
    pages = []
    docs = db.scalars(select(Document).where(Document.job_id == job.id,
                                             Document.status == DocumentStatus.STORED)).all()
    for doc in docs:
        for page in db.scalars(select(FilePage).where(
                FilePage.sha256 == doc.sha256, FilePage.pipeline_version == PIPELINE_VERSION)
                .order_by(FilePage.page_number)):
            if len(pages) >= max_images or not page.image_key:
                break
            key = f"reviews/{review.id}/{len(pages) + 1:02d}.jpg"
            try:
                storage.put(key, storage.get(page.image_key), "image/jpeg")
            except Exception as exc:  # noqa: BLE001 - a missing image must not stop the review
                log.warning("Review page image not copied: %s", exc)
                continue
            pages.append({"n": len(pages) + 1, "file": short_name(doc.file_name),
                          "page": page.page_number, "key": key})
    return pages


# ---------------------------------------------------------------- reviewer actions
def _require_open(review: Review) -> None:
    if review.status not in OPEN:
        raise ReviewError(f"Review DV-{review.number} is {review.status}; it can no longer change")


def start_review(review: Review, reviewer: str) -> None:
    _require_open(review)
    review.status, review.reviewer = "In Review", reviewer


def set_row(review: Review, row_id: str, decision: str, value: str | None = None,
            comment: str | None = None, reviewer: str | None = None) -> dict:
    _require_open(review)
    if decision not in DECISIONS:
        raise ReviewError(f"Decision must be one of {', '.join(DECISIONS)}")
    rows = [dict(r) for r in review.rows]
    row = next((r for r in rows if r["id"] == str(row_id)), None)
    if row is None:
        raise ReviewError(f"Row {row_id} not found")
    if decision == "Edit" and not (value or "").strip():
        raise ReviewError("Edit needs the corrected value")
    row.update(decision=decision, edited_value=(value or "").strip() or None if decision == "Edit"
               else None, comment=comment if comment is not None else row.get("comment"))
    review.rows = rows                      # reassign: JSON column change tracking
    if review.status == "Pending Review":
        review.status = "In Review"
    if reviewer and not review.reviewer:
        review.reviewer = reviewer
    return row


def approve(db: Session, review: Review, client: ZohoClient, reviewer: str, *,
            values_compared: bool, activate_account: bool = False, notes: str | None = None,
            triggers: list[str] | None = None) -> dict:
    _require_open(review)
    if not values_compared:
        raise ReviewError("Confirm that every approved value was compared with the document")
    rows = review.rows
    result = apply_rows(
        client, review.account_id,
        [{"id": r["id"], "zoho_field": r["zoho_field"], "decision": r["decision"],
          "value": r["edited_value"] if r["decision"] == "Edit" else r["proposed_value"],
          "current_value": r["current_value"]} for r in rows],
        dry_run=False, triggers=triggers, activate=activate_account,
        review_id=f"DV-{review.number}")
    if result.status is None:                          # e.g. the Account could not be read
        raise ReviewError(result.error or "Nothing was written")
    outcome = {r.row_id: r for r in result.rows}
    review.rows = [{**r, "outcome": outcome[r["id"]].outcome,
                    "outcome_detail": outcome[r["id"]].detail} for r in rows]
    review.status, review.reviewer = result.status, reviewer
    review.values_compared, review.activate_account = True, activate_account
    review.activated = result.activated
    review.reviewer_notes = "\n".join(filter(None, [notes, "Write-back:", *result.notes]))
    review.closed_at = utcnow()
    record_event(db, "ENGINE_REVIEW_APPROVED", f"DV-{review.number}: {result.status}",
                 review.job_id, {"reviewer": reviewer, "activated": result.activated,
                                 "rows": {r.zoho_field: r.outcome for r in result.rows}})
    return {"status": result.status, "activated": result.activated, "error": result.error,
            "rows": [{"id": r.row_id, "field": r.zoho_field, "outcome": r.outcome,
                      "detail": r.detail} for r in result.rows]}


def close_without_writing(db: Session, review: Review, reviewer: str, status: str,
                          notes: str) -> None:
    _require_open(review)
    if not (notes or "").strip():
        raise ReviewError("A note is required (what is wrong / which documents are needed)")
    review.status, review.reviewer = status, reviewer
    review.reviewer_notes, review.closed_at = notes.strip(), utcnow()
    record_event(db, "ENGINE_REVIEW_" + status.upper().replace(" ", "_"),
                 f"DV-{review.number}: {notes.strip()[:200]}", review.job_id,
                 {"reviewer": reviewer})


def purge_old_review_images(db: Session, storage: Storage, days: int) -> int:
    """Delete page-image copies of reviews closed more than `days` ago."""
    cutoff = utcnow() - timedelta(days=days)
    purged = 0
    for review in db.scalars(select(Review).where(Review.closed_at.is_not(None),
                                                  Review.closed_at < cutoff,
                                                  Review.images_purged.is_(False))):
        for p in review.pages:
            try:
                storage.delete(p["key"])
            except Exception:  # noqa: BLE001
                pass
        review.images_purged = True
        purged += 1
    db.commit()
    return purged


# ---------------------------------------------------------------- what the dashboard gets
def summary_view(r: Review) -> dict:
    checks = r.summary.get("activation_checks") or []
    return {"id": str(r.id), "number": f"DV-{r.number}", "account_id": r.account_id,
            "account_name": r.account_name, "customer_number": r.customer_number,
            "status": r.status, "recommendation": r.recommendation,
            "checks_met": f"{sum(c['status'] == 'PASS' for c in checks)}/{len(checks)}"
            if checks else None,
            "rows_to_decide": sum(1 for x in r.rows if x["decision"] == "Pending"
                                  and x["action"] in ("FILL", "CORRECT", "REVIEW_CONFLICT",
                                                      "HOLD", "DIFFERS", "NO_PICKLIST_VALUE")),
            "requested_by": r.requested_by, "reviewer": r.reviewer,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None}


def detail_view(r: Review) -> dict:
    return {**summary_view(r), "job_id": str(r.job_id), "summary": r.summary, "rows": r.rows,
            "pages": [{k: v for k, v in p.items() if k != "key"}
                      for p in ([] if r.images_purged else r.pages)],
            "reviewer_notes": r.reviewer_notes, "values_compared": r.values_compared,
            "activate_account": r.activate_account, "activated": r.activated}
