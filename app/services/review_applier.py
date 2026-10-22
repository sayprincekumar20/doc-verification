"""Apply reviewer-approved "Document Verifications" records to their Accounts automatically.

Runs from the scheduler (no call from Zoho to the engine is needed): finds records with
Review Status = Approved and applies them with the same rules as scripts/apply_reviews.py
(only Approve/Edit rows, Account re-read first, Conflict on changed fields). Each result is
written to the audit trail (and to field_updates when the review belongs to an engine job).
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import FieldUpdate, VerificationJob
from app.review.apply import ApplyResult, apply_review, approved_reviews
from app.services.audit import record_event
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError
from app.zoho.review_record import load_names

log = logging.getLogger(__name__)


def _job_of(db: Session, review: dict, names: dict) -> VerificationJob | None:
    raw = str(review.get(names["fields"]["Job ID"]) or "")
    try:
        return db.get(VerificationJob, uuid.UUID(raw))
    except ValueError:
        return None   # e.g. test records created offline ("offline:<account>")


def apply_approved(db: Session, client: ZohoClient, settings: Settings) -> list[ApplyResult]:
    names = load_names()
    try:
        reviews = approved_reviews(client, names)
    except ZohoError as exc:
        log.warning("Reading approved reviews failed: %s", exc)
        return []
    results = []
    for review in reviews:
        result = apply_review(client, review, dry_run=False, names=names,
                              triggers=settings.zoho_update_triggers)
        job = _job_of(db, review, names)
        rows = [{"field": r.zoho_field, "decision": r.decision, "outcome": r.outcome,
                 "detail": r.detail} for r in result.rows if r.outcome != "NOT_APPROVED"]
        if job is not None:
            for r in result.rows:
                if r.outcome in ("APPLIED", "CONFLICT", "FAILED"):
                    db.add(FieldUpdate(
                        job_id=job.id, account_id=job.account_id, zoho_field=r.zoho_field,
                        action="REVIEW_" + (r.decision or "").upper(), old_value=None,
                        new_value=None if r.value is None else str(r.value), confidence=None,
                        grounding=None, sources=None, mode="review",
                        status={"APPLIED": "APPLIED", "CONFLICT": "SKIPPED_CHANGED",
                                "FAILED": "FAILED"}[r.outcome],
                        detail=r.detail[:1000] or None))
        record_event(db, "REVIEW_APPLIED",
                     f"{review.get('Name') or result.review_id}: {result.status or 'not applied'}"
                     + (f" ({result.error})" if result.error else ""),
                     job.id if job else None,
                     {"review_id": result.review_id, "account_id": result.account_id,
                      "status": result.status, "rows": rows})
        db.commit()
        log.info("Review %s applied: %s", result.review_id, result.status)
        results.append(result)
    return results
