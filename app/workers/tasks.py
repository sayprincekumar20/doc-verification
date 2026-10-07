import logging
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.db.models import JobStatus, VerificationJob
from app.db.session import session_scope
from app.extraction.providers import TransientExtractionError, build_provider
from app.logging import setup_logging
from app.pipeline.assess import assess_job
from app.pipeline.collect import collect_job
from app.pipeline.extract import extract_job, extraction_version, manila_today
from app.pipeline.read import read_job
from app.services.audit import record_event
from app.storage.base import build_storage
from app.workers.celery_app import celery_app
from app.zoho.errors import ZohoAuthError, ZohoNotFoundError, ZohoTransientError
from app.zoho.factory import get_zoho_client

log = logging.getLogger(__name__)
MAX_RETRIES = 5


def _fail(job_id: uuid.UUID, message: str) -> None:
    with session_scope() as db:
        job = db.get(VerificationJob, job_id)
        if job:
            job.status, job.error = JobStatus.FAILED, message
            job.completed_at = datetime.now(UTC)
            record_event(db, "JOB_FAILED", message, job.id)


@celery_app.task(bind=True, name="collect_documents", max_retries=MAX_RETRIES)
def collect_documents(self, job_id: str) -> str:
    setup_logging(get_settings().log_level)
    jid = uuid.UUID(job_id)
    settings = get_settings()
    try:
        with session_scope() as db:
            job = db.get(VerificationJob, jid)
            if job is None:
                log.error("Job %s not found", job_id)
                return "missing"
            if job.status not in (JobStatus.QUEUED, JobStatus.COLLECTING):
                return job.status  # already processed: duplicate delivery is a no-op
            collect_job(db, job, get_zoho_client(), build_storage(settings), settings)
            status = job.status
        if status == JobStatus.COLLECTED:
            read_documents.delay(job_id)  # Phase 1B
        return status
    except ZohoTransientError as exc:
        if self.request.retries < MAX_RETRIES:
            raise self.retry(exc=exc, countdown=min(60 * 2**self.request.retries, 1800)) from exc
        _fail(jid, f"Zoho unavailable after retries: {exc}")
    except (ZohoAuthError, ZohoNotFoundError) as exc:
        _fail(jid, str(exc))
    except Exception as exc:  # unexpected: record and stop, don't loop
        log.exception("Collection crashed", extra={"job_id": job_id})
        _fail(jid, f"Unexpected error: {type(exc).__name__}: {exc}")
    return JobStatus.FAILED


@celery_app.task(name="read_documents", soft_time_limit=1800)
def read_documents(job_id: str) -> str:
    """Phase 1B: OCR + classify every stored file of the job (cached per file content)."""
    setup_logging(get_settings().log_level)
    jid = uuid.UUID(job_id)
    try:
        with session_scope() as db:
            job = db.get(VerificationJob, jid)
            if job is None or job.status not in (JobStatus.COLLECTED, JobStatus.READING):
                return job.status if job else "missing"
            read_job(db, job, build_storage(get_settings()))
            status = job.status
        if status == JobStatus.READ and get_settings().extraction_provider != "none":
            with session_scope() as db:
                db.get(VerificationJob, jid).status = JobStatus.EXTRACTING
            extract_documents.delay(job_id)  # Phase 1C
            return JobStatus.EXTRACTING
        return status
    except Exception as exc:
        log.exception("Reading crashed", extra={"job_id": job_id})
        _fail(jid, f"Reading failed: {type(exc).__name__}: {exc}")
    return JobStatus.FAILED


def build_vision_provider():
    s = get_settings()
    key = {"anthropic": s.anthropic_api_key, "openai": s.openai_api_key}.get(
        s.extraction_provider)
    return build_provider(s.extraction_provider, key.get_secret_value() if key else None,
                          s.extraction_model, reasoning_effort=s.extraction_reasoning_effort)


@celery_app.task(bind=True, name="extract_documents", max_retries=5, soft_time_limit=1800)
def extract_documents(self, job_id: str) -> str:
    """Phase 1C: vision-model extraction, grounded against OCR text (cached per content)."""
    setup_logging(get_settings().log_level)
    jid = uuid.UUID(job_id)
    try:
        provider = build_vision_provider()
        with session_scope() as db:
            job = db.get(VerificationJob, jid)
            if job is None or job.status != JobStatus.EXTRACTING:
                return job.status if job else "missing"
            extract_job(db, job, build_storage(get_settings()), provider)
            assess_job(db, job, extraction_version(provider), manila_today())  # Phase 1D
            return job.status
    except TransientExtractionError as exc:
        if self.request.retries < 5:
            raise self.retry(exc=exc, countdown=min(60 * 2**self.request.retries, 1800)) from exc
        _fail(jid, f"AI provider unavailable after retries: {exc}")
    except Exception as exc:
        log.exception("Extraction crashed", extra={"job_id": job_id})
        _fail(jid, f"Extraction failed: {type(exc).__name__}: {exc}")
    return JobStatus.FAILED
