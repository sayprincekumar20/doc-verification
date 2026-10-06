"""Find every document file on a Zoho Account (no database dependencies).

Sources:
  1. Account > Attachments related list
  2. Account fileupload fields (Business_Permit, BIR_Registration_COR, ...)
  3. Attachments on the Account's Notes
Used by the worker (pipeline/collect.py) and the standalone fetch script.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app.config import Settings
from app.domain import DocumentSource
from app.zoho.client import ZohoClient

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
