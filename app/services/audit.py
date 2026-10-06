import uuid

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record_event(
    db: Session,
    event_type: str,
    message: str = "",
    job_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(job_id=job_id, event_type=event_type, message=message, payload=payload)
    db.add(event)
    return event
