"""Database models for Phase 1A (jobs, collected documents, stored files, audit trail).

Later phases add tables via new Alembic migrations (pages, extractions, proposals, ...).
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain import DocumentSource  # noqa: F401  (re-exported for callers)


def utcnow() -> datetime:
    return datetime.now(UTC)


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    COLLECTING = "COLLECTING"
    COLLECTED = "COLLECTED"          # documents stored, reading queued
    READING = "READING"              # OCR + classification running
    READ = "READ"                    # every stored file has pages + text (extraction is next)
    NO_DOCUMENTS = "NO_DOCUMENTS"    # nothing usable found on the account
    FAILED = "FAILED"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.COLLECTING, JobStatus.COLLECTED,
                       JobStatus.READING)
_ACTIVE_SQL = "status IN ('QUEUED', 'COLLECTING', 'COLLECTED', 'READING')"


class DocumentStatus(enum.StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"              # same bytes as another document in this job
    REJECTED = "REJECTED"                # unsupported type, empty or too large
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"


class VerificationJob(Base):
    __tablename__ = "verification_jobs"
    __table_args__ = (
        # At most one active job per account: makes the CRM button idempotent at DB level.
        Index(
            "uq_active_job_per_account",
            "account_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SQL),
            sqlite_where=text(_ACTIVE_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_number: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="crm_button")
    # What the CRM button reported. The engine re-checks everything itself.
    reported_document_status: Mapped[str | None] = mapped_column(String(8))
    reported_attachment_count: Mapped[int | None] = mapped_column()
    requested_by_user_id: Mapped[str | None] = mapped_column(String(32))
    requested_by_email: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Account fields as read from Zoho when the job ran (expected values for later phases).
    account_snapshot: Mapped[dict | None] = mapped_column(JSON)
    # Non-fatal issues, e.g. [{"code": "ATTACHMENT_COUNT_MISMATCH", "message": "..."}]
    warnings: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED, index=True)
    documents_found: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["Document"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Document.created_at"
    )


class StoredFile(Base):
    """One row per unique file content (content-addressed by SHA-256)."""

    __tablename__ = "stored_files"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(128))
    extension: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """One file reference found on the account during a job."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("job_id", "source", "zoho_file_ref", name="uq_document_source_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    source_field: Mapped[str | None] = mapped_column(String(64))   # fileupload field api name
    zoho_parent_id: Mapped[str | None] = mapped_column(String(32))  # note id for note files
    zoho_file_ref: Mapped[str] = mapped_column(String(255))         # attachment id / file id
    file_name: Mapped[str | None] = mapped_column(String(512))
    zoho_created_time: Mapped[str | None] = mapped_column(String(40))
    zoho_uploaded_by: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(ForeignKey("stored_files.sha256"), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    status_detail: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[VerificationJob] = relationship(back_populates="documents")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_jobs.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileReading(Base):
    """Result of reading one file's content with one pipeline version. Keyed by content hash,
    so the same file uploaded again (or re-checked later) is never OCR'd twice."""

    __tablename__ = "file_readings"

    sha256: Mapped[str] = mapped_column(ForeignKey("stored_files.sha256"), primary_key=True)
    pipeline_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))          # READ | UNREADABLE
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(default=0)
    document_type: Mapped[str | None] = mapped_column(String(32))  # from the first page
    type_confidence: Mapped[float | None] = mapped_column()
    seconds: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FilePage(Base):
    __tablename__ = "file_pages"
    __table_args__ = (UniqueConstraint("sha256", "pipeline_version", "page_number",
                                       name="uq_file_page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(ForeignKey("stored_files.sha256"), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(32))
    page_number: Mapped[int] = mapped_column()
    source: Mapped[str] = mapped_column(String(16))           # pdf_text | pdf_scan | image | office
    text_method: Mapped[str] = mapped_column(String(16))      # text_layer | ocr | ocr+ink
    text: Mapped[str] = mapped_column(Text)
    grounding_text: Mapped[str] = mapped_column(Text)
    ocr_conf: Mapped[float] = mapped_column()
    word_count: Mapped[int] = mapped_column()
    quality_label: Mapped[str] = mapped_column(String(16))    # GOOD | FAIR | POOR | UNREADABLE
    quality_score: Mapped[float] = mapped_column()
    quality_reasons: Mapped[list | None] = mapped_column(JSON)
    rotation: Mapped[int] = mapped_column(default=0)
    cropped: Mapped[bool] = mapped_column(default=False)
    skew: Mapped[float] = mapped_column(default=0.0)
    document_type: Mapped[str] = mapped_column(String(32))
    type_confidence: Mapped[float] = mapped_column()
    type_signals: Mapped[list | None] = mapped_column(JSON)
    image_key: Mapped[str] = mapped_column(String(512))       # cleaned page image in storage
    seconds: Mapped[float] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
