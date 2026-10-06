"""Phase 1B: read every stored file of a job (normalize, clean, OCR, classify).

Results are keyed by file content (SHA-256) + pipeline version, so unchanged files are never
read twice, across jobs and accounts.
"""

import logging
import time
from datetime import UTC, datetime

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Document,
    DocumentStatus,
    FilePage,
    FileReading,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.reading.classify import classify_text
from app.reading.normalize import UnreadableFileError
from app.reading.pipeline import read_document
from app.services.audit import record_event
from app.storage.base import Storage

log = logging.getLogger(__name__)

# Bump when preprocessing/OCR/classification changes, so files are re-read with the new logic.
PIPELINE_VERSION = "read-1.0"


def _jpeg(image) -> bytes:
    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    return cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def read_file(db: Session, storage: Storage, stored: StoredFile, file_name: str | None,
              version: str = PIPELINE_VERSION) -> FileReading:
    existing = db.get(FileReading, (stored.sha256, version))
    if existing is not None:
        return existing  # already read this exact content

    started = time.monotonic()
    try:
        pages = read_document(storage.get(stored.storage_key), stored.mime_type)
    except UnreadableFileError as exc:
        reading = FileReading(sha256=stored.sha256, pipeline_version=version,
                              status="UNREADABLE", error=str(exc), page_count=0)
        db.add(reading)
        db.flush()
        return reading

    first_type, first_conf = None, None
    for page in pages:
        cls = classify_text(page.grounding_text, file_name)
        if first_type is None:
            first_type, first_conf = cls.document_type, cls.confidence
        key = f"pages/{version}/{stored.sha256[:2]}/{stored.sha256}/p{page.page_number}.jpg"
        storage.put(key, _jpeg(page.prepared.color), "image/jpeg")
        q = page.quality
        db.add(FilePage(
            sha256=stored.sha256, pipeline_version=version, page_number=page.page_number,
            source=page.source, text_method=page.text_method, text=page.text,
            grounding_text=page.grounding_text, ocr_conf=q.ocr_conf, word_count=q.words,
            quality_label=q.label, quality_score=q.score, quality_reasons=q.reasons,
            rotation=page.prepared.rotation, cropped=page.prepared.cropped,
            skew=page.prepared.skew, document_type=cls.document_type,
            type_confidence=cls.confidence, type_signals=cls.signals[:10], image_key=key,
            seconds=page.seconds,
        ))
    all_unreadable = all(p.quality.label == "UNREADABLE" for p in pages)
    reading = FileReading(
        sha256=stored.sha256, pipeline_version=version,
        status="UNREADABLE" if all_unreadable else "READ",
        error="No readable text (blurry, dark or not a document)" if all_unreadable else None,
        page_count=len(pages), document_type=first_type, type_confidence=first_conf,
        seconds=round(time.monotonic() - started, 2),
    )
    db.add(reading)
    db.flush()
    return reading


def read_job(db: Session, job: VerificationJob, storage: Storage,
             version: str = PIPELINE_VERSION) -> VerificationJob:
    job.status = JobStatus.READING
    db.commit()
    docs = db.scalars(select(Document).where(Document.job_id == job.id,
                                             Document.status == DocumentStatus.STORED)).all()
    summary: list[dict] = []
    for doc in docs:
        stored = db.get(StoredFile, doc.sha256)
        reading = read_file(db, storage, stored, doc.file_name, version)
        summary.append({"file": doc.file_name, "status": reading.status,
                        "type": reading.document_type, "pages": reading.page_count})
        db.commit()  # keep finished files even if a later one crashes
    job.status = JobStatus.READ
    record_event(db, "READING_FINISHED", f"{len(docs)} file(s) read", job.id,
                 {"files": summary, "pipeline_version": version})
    db.commit()
    log.info("Reading finished", extra={"job_id": str(job.id)})
    return job


def mark_failed(db: Session, job: VerificationJob, message: str) -> None:
    job.status, job.error, job.completed_at = JobStatus.FAILED, message, datetime.now(UTC)
    record_event(db, "JOB_FAILED", message, job.id)
