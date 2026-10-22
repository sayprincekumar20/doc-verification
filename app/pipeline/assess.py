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
from app.extraction.validate import check_validity
from app.services.audit import record_event

_VALIDITY_CODES = {"EXPIRED", "NOT_YET_VALID", "VALIDITY_DERIVED", "UNUSUAL_VALIDITY_PERIOD",
                   "NO_EXPIRY_PRINTED"}
_DATED_TYPES = {"DTI_BN_CERT", "MAYORS_PERMIT", "GOVERNMENT_ID", "BIR_2303"}


def _current_validity(row: DocumentExtraction, today: date) -> tuple:
    """Re-apply the current validity rules (they can change after a file was extracted)."""
    if row.document_type not in _DATED_TYPES:
        return row.validity_status, row.valid_until, row.issues or []
    normalized = {k: (v or {}).get("normalized") for k, v in (row.fields or {}).items()}
    v = check_validity(row.document_type, normalized, today)
    kept = [i for i in (row.issues or []) if i.get("code") not in _VALIDITY_CODES]
    return v.status, v.valid_until, kept + [i.as_dict() for i in v.issues]


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
        status, until, issues = _current_validity(row, today)
        inputs.append(DocInput(str(doc.id), doc.file_name or doc.zoho_file_ref,
                               row.document_type or "OTHER", row.fields or {},
                               status, until, issues, row.other_fields or []))
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
