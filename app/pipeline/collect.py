"""Phase 1A: collect every document attached to a Zoho Account.

Sources:
  1. Account > Attachments related list
  2. Account fileupload fields (Business_Permit, BIR_Registration_COR, ...)
  3. Attachments on the Account's Notes
Each file is validated, hashed (SHA-256), stored once, and recorded as a Document row.
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.pipeline.file_checks import check_file
from app.services.audit import record_event
from app.storage.base import Storage, storage_key_for
from app.zoho.client import ZohoClient
from app.zoho.errors import FileTooLargeError, ZohoAPIError, ZohoTransientError

log = logging.getLogger(__name__)
MODULE = "Accounts"


@dataclass
class SourceRef:
    source: DocumentSource
    file_ref: str
    file_name: str | None
    download: Callable[[], bytes] = field(repr=False)
    source_field: str | None = None
    parent_id: str | None = None
    created_time: str | None = None
    uploaded_by: str | None = None


def _who(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("email") or value.get("name")
    return None


def discover_sources(
    client: ZohoClient,
    account_id: str,
    account: dict[str, Any],
    settings: Settings,
    attachments: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> list[SourceRef]:
    limit = settings.max_file_bytes
    refs: list[SourceRef] = []
    if attachments is None:
        attachments = client.list_attachments(MODULE, account_id)
    if notes is None:
        notes = client.list_notes(MODULE, account_id)

    for att in attachments:
        att_id = str(att["id"])
        refs.append(SourceRef(
            source=DocumentSource.ATTACHMENT,
            file_ref=att_id,
            file_name=att.get("File_Name"),
            created_time=att.get("Created_Time"),
            uploaded_by=_who(att.get("Created_By")),
            download=lambda a=att_id: client.download_attachment(MODULE, account_id, a, limit),
        ))

    for field_name in settings.zoho_file_fields:
        for item in account.get(field_name) or []:
            attachment_id = item.get("attachment_Id")
            file_id = item.get("File_Id__s")
            if attachment_id:
                download = partial(client.download_field_attachment, MODULE, account_id,
                                   str(attachment_id), limit)
            elif file_id:
                download = partial(client.download_file, file_id, limit)
            else:
                continue
            refs.append(SourceRef(
                source=DocumentSource.FILE_FIELD,
                source_field=field_name,
                file_ref=str(attachment_id or file_id),
                file_name=item.get("File_Name__s"),
                download=download,
            ))

    for note in notes:
        note_id = str(note["id"])
        for att in note.get("$attachments") or []:
            att_id = str(att["id"])
            refs.append(SourceRef(
                source=DocumentSource.NOTE_ATTACHMENT,
                parent_id=note_id,
                file_ref=att_id,
                file_name=att.get("File_Name") or att.get("file_Name"),
                created_time=att.get("Created_Time"),
                download=lambda n=note_id, a=att_id: client.download_attachment(
                    "Notes", n, a, limit
                ),
            ))
    return refs


def build_snapshot(account: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Keep only the Account fields later phases need (not the whole 150-field record)."""
    return {name: account.get(name) for name in fields}


def add_warning(job: VerificationJob, code: str, message: str, **details: Any) -> None:
    # Reassign (not append) so SQLAlchemy detects the JSON change.
    job.warnings = [*(job.warnings or []), {"code": code, "message": message, **details}]


def _check_button_report(job: VerificationJob, refs: list[SourceRef]) -> None:
    """Compare what the CRM button reported with what the engine actually found."""
    total = len(refs)
    attachments = sum(1 for r in refs if r.source == DocumentSource.ATTACHMENT)
    reported = job.reported_attachment_count
    if reported is not None and reported not in (attachments, total):
        add_warning(job, "ATTACHMENT_COUNT_MISMATCH",
                    "Button counted a different number of files than the engine found; "
                    "files may have been added or removed after the click.",
                    reported=reported, found_attachments=attachments, found_total=total)
    if job.reported_document_status == "NO" and total > 0:
        add_warning(job, "DOCUMENT_STATUS_MISMATCH",
                    "Button reported no documents, but the engine found some.", found_total=total)
    if job.reported_document_status == "YES" and total == 0:
        add_warning(job, "DOCUMENT_STATUS_MISMATCH",
                    "Button reported documents, but the engine found none.")


def _store_file(db: Session, storage: Storage, data: bytes, sha: str, ext: str, mime: str) -> None:
    if db.get(StoredFile, sha) is not None:
        return  # same content collected in an earlier job: reuse it
    key = storage_key_for(sha, ext)
    if not storage.exists(key):
        storage.put(key, data, mime)
    db.add(StoredFile(sha256=sha, size_bytes=len(data), mime_type=mime, extension=ext,
                      storage_key=key))
    db.flush()


def collect_job(
    db: Session,
    job: VerificationJob,
    client: ZohoClient,
    storage: Storage,
    settings: Settings,
) -> VerificationJob:
    """Collect documents for one job. Raises ZohoTransientError when the account itself
    can't be read (the worker retries the whole job)."""
    job.status = JobStatus.COLLECTING
    db.commit()

    # GET /crm/v8/Accounts/{account_id}
    account = client.get_record(MODULE, job.account_id)
    job.account_snapshot = build_snapshot(account, settings.zoho_snapshot_fields)
    zoho_number = account.get("Inventory_Customer_Number")
    if job.customer_number and zoho_number and job.customer_number != zoho_number:
        add_warning(job, "CUSTOMER_NUMBER_MISMATCH",
                    "customer_number from the button differs from the Account in Zoho.",
                    reported=job.customer_number, zoho=zoho_number)
    if not job.customer_number:
        job.customer_number = zoho_number
    record_event(db, "ACCOUNT_READ", account.get("Account_Name") or "", job.id,
                 {"customer_status": account.get("Customer_Status"),
                  "modified_time": account.get("Modified_Time")})

    # GET .../Attachments, fileupload fields, GET .../Notes ($attachments)
    refs = discover_sources(client, job.account_id, account, settings)
    _check_button_report(job, refs)
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.source] = counts.get(r.source, 0) + 1
    record_event(db, "SOURCES_DISCOVERED", f"{len(refs)} file reference(s) found",
                 job.id, {"by_source": counts})

    by_sha: dict[str, Document] = {}
    transient_failures = 0

    for ref in refs:
        doc = Document(
            job_id=job.id, source=ref.source, source_field=ref.source_field,
            zoho_parent_id=ref.parent_id, zoho_file_ref=ref.file_ref, file_name=ref.file_name,
            zoho_created_time=ref.created_time, zoho_uploaded_by=ref.uploaded_by,
            status=DocumentStatus.STORED,
        )
        db.add(doc)
        try:
            data = ref.download()
        except FileTooLargeError as exc:
            doc.status, doc.status_detail = DocumentStatus.REJECTED, str(exc)
            continue
        except ZohoTransientError as exc:
            transient_failures += 1
            doc.status, doc.status_detail = DocumentStatus.DOWNLOAD_FAILED, str(exc)
            continue
        except ZohoAPIError as exc:
            doc.status, doc.status_detail = DocumentStatus.DOWNLOAD_FAILED, str(exc)
            continue

        doc.size_bytes = len(data)
        check = check_file(data, ref.file_name, settings.max_file_bytes)
        if not check.ok:
            doc.status, doc.status_detail = DocumentStatus.REJECTED, check.reason
            continue

        sha = hashlib.sha256(data).hexdigest()
        doc.mime_type = check.mime_type
        if sha in by_sha:
            doc.sha256 = sha  # its StoredFile row already exists
            doc.status = DocumentStatus.DUPLICATE
            doc.duplicate_of_id = by_sha[sha].id
            doc.status_detail = f"Same content as {by_sha[sha].file_name}"
            continue

        # Store the file row first, then point the document at it (foreign key order).
        with db.no_autoflush:
            _store_file(db, storage, data, sha, check.extension, check.mime_type)  # type: ignore[arg-type]
        doc.sha256 = sha
        db.flush()
        by_sha[sha] = doc

    stored = len(by_sha)
    job.documents_found = stored
    if stored:
        job.status = JobStatus.COLLECTED
    elif transient_failures:
        job.status = JobStatus.FAILED
        job.error = "Files exist but could not be downloaded from Zoho; retry later"
        job.completed_at = datetime.now(UTC)
    else:
        job.status = JobStatus.NO_DOCUMENTS
        job.completed_at = datetime.now(UTC)

    status_counts: dict[str, int] = {}
    for d in job.documents:
        status_counts[d.status] = status_counts.get(d.status, 0) + 1
    record_event(db, "COLLECTION_FINISHED", f"Job status {job.status}", job.id,
                 {"documents": status_counts, "warnings": job.warnings or []})
    db.commit()
    log.info("Collection finished: %s stored, status %s", stored, job.status,
             extra={"job_id": str(job.id), "account_id": job.account_id})
    return job
