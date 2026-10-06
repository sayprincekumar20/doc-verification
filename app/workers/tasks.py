import logging
import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.db.models import JobStatus, VerificationJob
from app.db.session import session_scope
from app.logging import setup_logging
from app.pipeline.collect import collect_job
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
            return job.status
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
