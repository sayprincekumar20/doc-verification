"""Phase 1D job step: assess the customer from the job's extracted documents."""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assessment.assess import DocInput, assess
from app.db.models import (
    Document,
    DocumentExtraction,
    DocumentStatus,
    JobAssessment,
    JobStatus,
    VerificationJob,
)
from app.services.audit import record_event


def assess_job(db: Session, job: VerificationJob, extraction_version: str,
               today: date) -> JobAssessment:
    docs = db.scalars(select(Document).where(Document.job_id == job.id,
                                             Document.status == DocumentStatus.STORED)).all()
    inputs, not_extracted = [], []
    for doc in docs:
        row = db.get(DocumentExtraction, (doc.sha256, extraction_version))
        if row is None or row.status != "EXTRACTED":
            not_extracted.append({"file": doc.file_name,
                                  "status": row.status if row else "MISSING",
                                  "error": row.error if row else None})
            continue
        inputs.append(DocInput(str(doc.id), doc.file_name or doc.zoho_file_ref,
                               row.document_type or "OTHER", row.fields or {},
                               row.validity_status, row.valid_until, row.issues or [],
                               row.other_fields or []))
    result = assess(job.account_snapshot or {}, inputs, today)
    result["not_extracted"] = not_extracted
    result["skipped_documents"] = [
        {"file": d.file_name, "status": d.status, "detail": d.status_detail}
        for d in db.scalars(select(Document).where(Document.job_id == job.id,
                                                   Document.status != DocumentStatus.STORED))]
    existing = db.get(JobAssessment, job.id)
    row = existing or JobAssessment(job_id=job.id)
    row.rules_version, row.recommendation = result["rules_version"], result["recommendation"]
    row.assessment = result
    db.merge(row)
    job.status, job.completed_at = JobStatus.ASSESSED, datetime.now(UTC)
    record_event(db, "ASSESSED", f"Recommendation: {result['recommendation']}", job.id,
                 {"reasons": result["reasons"],
                  "proposals": {p["zoho_field"]: p["action"] for p in result["proposals"]}})
    db.commit()
    return db.get(JobAssessment, job.id)
