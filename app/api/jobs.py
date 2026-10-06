import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_enqueuer, require_api_key
from app.db.models import VerificationJob
from app.db.session import get_db
from app.schemas import JobCreate, JobCreated, JobOut
from app.services.jobs import create_or_get_active_job

router = APIRouter(prefix="/v1/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    req: JobCreate,
    response: Response,
    db: Session = Depends(get_db),
    enqueue: Callable[[str], None] = Depends(get_enqueuer),
) -> JobCreated:
    job, created = create_or_get_active_job(db, req)
    if created:
        enqueue(str(job.id))
        message = f"Verification started ({job.id}). Results will appear for review."
    else:
        response.status_code = status.HTTP_200_OK
        message = f"Verification already in progress ({job.id})."
    return JobCreated(job_id=job.id, status=job.status, created=created, message=message)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> VerificationJob:
    job = db.get(VerificationJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
