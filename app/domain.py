"""Plain enums shared by the pipeline and the database models (no database dependencies)."""

import enum


class DocumentSource(enum.StrEnum):
    ATTACHMENT = "ATTACHMENT"            # Account > Attachments related list
    FILE_FIELD = "FILE_FIELD"            # Account fileupload fields (Business_Permit, ...)
    NOTE_ATTACHMENT = "NOTE_ATTACHMENT"  # files attached to the Account's Notes
