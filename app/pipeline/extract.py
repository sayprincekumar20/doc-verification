"""Phase 1C job step: extract fields from every read document (cached per content + version)."""

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Document,
    DocumentExtraction,
    DocumentStatus,
    FilePage,
    FileReading,
    JobStatus,
    VerificationJob,
)
from app.extraction.extract import PageInput, extract_document
from app.extraction.prompts import EXTRACTION_PROMPT_VERSION
from app.extraction.providers import (
    ExtractionError,
    TransientExtractionError,
    VisionProvider,
)
from app.pipeline.read import PIPELINE_VERSION
from app.services.audit import record_event
from app.storage.base import Storage

log = logging.getLogger(__name__)
MANILA = timezone(timedelta(hours=8))


def manila_today() -> date:
    return datetime.now(MANILA).date()


def extraction_version(provider: VisionProvider) -> str:
    return f"{EXTRACTION_PROMPT_VERSION}|{provider.name}:{provider.model}|{PIPELINE_VERSION}"


def extract_file(db: Session, storage: Storage, sha256: str, provider: VisionProvider,
                 today: date) -> DocumentExtraction:
    version = extraction_version(provider)
    existing = db.get(DocumentExtraction, (sha256, version))
    if existing is not None and existing.status == "EXTRACTED":
        return existing

    reading = db.get(FileReading, (sha256, PIPELINE_VERSION))
    row = existing or DocumentExtraction(sha256=sha256, extraction_version=version)
    if reading is None or reading.status != "READ":
        row.status, row.error = "SKIPPED", "File was not readable"
        db.merge(row)
        db.flush()
        return row

    pages = db.scalars(select(FilePage).where(
        FilePage.sha256 == sha256, FilePage.pipeline_version == PIPELINE_VERSION)
        .order_by(FilePage.page_number)).all()
    inputs = [PageInput(storage.get(p.image_key), p.grounding_text, p.quality_label)
              for p in pages if p.quality_label != "UNREADABLE"]
    try:
        result = extract_document(inputs, reading.document_type or "OTHER", provider, today)
    except TransientExtractionError:
        raise  # worker retries the job later
    except ExtractionError as exc:
        row.status, row.error = "FAILED", str(exc)[:1000]
        row.expected_type = reading.document_type
        db.merge(row)
        db.flush()
        return row

    row.status, row.error = "EXTRACTED", None
    row.expected_type, row.model_type = result.expected_type, result.model_type
    row.document_type = result.document_type
    row.fields = {k: asdict(v) for k, v in result.fields.items()}
    row.issues = result.issues
    row.validity_status, row.valid_until = result.validity_status, result.valid_until
    row.model, row.calls, row.seconds = result.model, result.calls, result.seconds
    row.input_tokens, row.output_tokens = result.input_tokens, result.output_tokens
    row = db.merge(row)
    db.flush()
    return row


def extract_job(db: Session, job: VerificationJob, storage: Storage, provider: VisionProvider,
                today: date | None = None) -> VerificationJob:
    today = today or manila_today()
    job.status = JobStatus.EXTRACTING
    db.commit()
    docs = db.scalars(select(Document).where(Document.job_id == job.id,
                                             Document.status == DocumentStatus.STORED)).all()
    summary = []
    for doc in docs:
        row = extract_file(db, storage, doc.sha256, provider, today)
        summary.append({"file": doc.file_name, "status": row.status, "type": row.document_type,
                        "validity": row.validity_status,
                        "issues": [i["code"] for i in (row.issues or [])
                                   if i.get("severity") != "INFO"]})
        db.commit()
    job.status = JobStatus.EXTRACTED
    record_event(db, "EXTRACTION_FINISHED", f"{len(docs)} document(s) extracted", job.id,
                 {"documents": summary, "extraction_version": extraction_version(provider)})
    db.commit()
    return job
