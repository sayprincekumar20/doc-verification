"""Phase 3: create the Zoho "Document Verifications" review record for an assessed job and
attach the cleaned page images, so a person can review the proposals inside Zoho CRM.
One record per job (the Zoho id is stored on job_assessments)."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentStatus, FilePage, JobAssessment, VerificationJob
from app.pipeline.read import PIPELINE_VERSION
from app.services.audit import record_event
from app.storage.base import Storage
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError
from app.zoho.review_record import build_record, load_names

log = logging.getLogger(__name__)


def create_review_record(db: Session, job: VerificationJob, client: ZohoClient,
                         storage: Storage, max_images: int = 10) -> str | None:
    row = db.get(JobAssessment, job.id)
    if row is None:
        return None
    if row.zoho_review_id:
        return row.zoho_review_id  # never two records for one job
    names = load_names()
    record = build_record(row.assessment, job.account_id, job_id=str(job.id),
                          requested_by=job.requested_by_email, request_reason=job.reason,
                          names=names)
    try:
        review_id = client.create_record(names["module"], record)
    except ZohoError as exc:
        row.review_error = f"create: {exc}"[:1000]
        record_event(db, "REVIEW_RECORD_FAILED", row.review_error, job.id)
        db.commit()
        log.warning("Review record not created: %s", exc, extra={"job_id": str(job.id)})
        return None
    row.zoho_review_id = review_id

    errors, attached = [], 0
    docs = db.scalars(select(Document).where(Document.job_id == job.id,
                                             Document.status == DocumentStatus.STORED)).all()
    for doc in docs:
        pages = db.scalars(select(FilePage).where(
            FilePage.sha256 == doc.sha256, FilePage.pipeline_version == PIPELINE_VERSION)
            .order_by(FilePage.page_number)).all()
        for page in pages:
            if attached >= max_images:
                break
            name = f"{doc.file_name or doc.zoho_file_ref} - page {page.page_number}.jpg"
            try:
                client.upload_attachment(names["module"], review_id, name,
                                         storage.get(page.image_key), "image/jpeg")
                attached += 1
            except ZohoError as exc:
                errors.append(f"{name}: {exc}"[:300])
    row.review_error = "; ".join(errors)[:1000] or None
    record_event(db, "REVIEW_RECORD_CREATED", f"Zoho review record {review_id}", job.id,
                 {"zoho_review_id": review_id, "images_attached": attached,
                  "attachment_errors": errors})
    db.commit()
    return review_id
