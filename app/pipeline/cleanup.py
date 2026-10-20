"""Delete document copies after a job (KEEP_DOCUMENT_FILES=false).

Removed: the downloaded original files, cleaned page images, and page OCR text (file_pages)
plus their reading records (file_readings), for every file of the job.
Kept: file fingerprints/metadata (stored_files), extraction results (document_extractions,
which make re-checks free of AI cost), assessments, review links, alerts and the audit trail.
A file still used by another running job is left for that job's cleanup.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    ACTIVE_JOB_STATUSES,
    Document,
    FilePage,
    FileReading,
    StoredFile,
    VerificationJob,
)
from app.services.audit import record_event
from app.storage.base import Storage

log = logging.getLogger(__name__)


def purge_job_files(db: Session, job: VerificationJob, storage: Storage) -> dict:
    shas = {d.sha256 for d in db.scalars(select(Document).where(Document.job_id == job.id))
            if d.sha256}
    in_use = set(db.scalars(
        select(Document.sha256).join(VerificationJob, Document.job_id == VerificationJob.id)
        .where(Document.sha256.in_(shas), VerificationJob.id != job.id,
               VerificationJob.status.in_([s.value for s in ACTIVE_JOB_STATUSES]))))
    files = images = 0
    errors = []
    for sha in sorted(shas - in_use):
        for page in db.scalars(select(FilePage).where(FilePage.sha256 == sha)):
            try:
                storage.delete(page.image_key)
                images += 1
            except Exception as exc:  # noqa: BLE001 - keep going, report below
                errors.append(f"{page.image_key}: {exc}")
        db.execute(delete(FilePage).where(FilePage.sha256 == sha))
        db.execute(delete(FileReading).where(FileReading.sha256 == sha))
        stored = db.get(StoredFile, sha)
        if stored is not None:
            try:
                storage.delete(stored.storage_key)
                files += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{stored.storage_key}: {exc}")
    summary = {"files_deleted": files, "page_images_deleted": images,
               "kept_for_other_jobs": len(in_use), "errors": errors[:20]}
    record_event(db, "DOCUMENT_COPIES_DELETED",
                 f"{files} file(s), {images} page image(s) deleted", job.id, summary)
    db.commit()
    log.info("Document copies deleted: %s", summary, extra={"job_id": str(job.id)})
    return summary
