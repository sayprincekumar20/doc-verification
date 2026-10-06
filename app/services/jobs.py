import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ACTIVE_JOB_STATUSES, VerificationJob
from app.schemas import JobCreate
from app.services.audit import record_event


def find_active_job(db: Session, account_id: str) -> VerificationJob | None:
    return db.scalar(
        select(VerificationJob).where(
            VerificationJob.account_id == account_id,
            VerificationJob.status.in_([s.value for s in ACTIVE_JOB_STATUSES]),
        )
    )


def create_or_get_active_job(db: Session, req: JobCreate) -> tuple[VerificationJob, bool]:
    """Return (job, created). A second click while a job is running returns the same job."""
    existing = find_active_job(db, req.account_id)
    if existing:
        return existing, False

    who = req.requested_by
    job = VerificationJob(
        id=uuid.uuid4(),
        account_id=req.account_id,
        customer_number=req.customer_number,
        reason=req.reason,
        source=req.source,
        reported_document_status=req.document_status,
        reported_attachment_count=req.attachment_count,
        requested_by_user_id=who.id if who else None,
        requested_by_email=who.email if who else None,
        requested_at=req.requested_at,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with a simultaneous request; the unique index kept us safe.
        db.rollback()
        existing = find_active_job(db, req.account_id)
        if existing:
            return existing, False
        raise
    record_event(db, "JOB_CREATED", "Verification requested", job.id,
                 req.model_dump(mode="json", exclude={"account_id"}))
    db.commit()
    return job, True
