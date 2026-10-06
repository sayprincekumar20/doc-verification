import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MANILA = timezone(timedelta(hours=8))


class RequestedBy(BaseModel):
    """The sales rep who clicked "Verify Account"."""

    id: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)


class JobCreate(BaseModel):
    """Payload sent by the Zoho CRM "Verify Account" button.

    {
    "account_id": "1000000000000001",
    "customer_number": "EXAMPLE-001",
      "reason": "for activation po Ma'am",
      "document_status": "YES",
      "attachment_count": 3,
    "requested_by": {"id": "1000000000000003", "email": "reviewer@example.com"},
      "requested_at": "2026-10-05T15:36:00+08:00",
      "source": "crm_button"
    }
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    account_id: str = Field(pattern=r"^\d{10,25}$", description="Zoho Account record id")
    customer_number: str | None = Field(default=None, max_length=64,
                                        description="Inventory_Customer_Number, e.g. EXAMPLE-001")
    reason: str = Field(default="", max_length=500, description="Optional; may be blank")
    document_status: Literal["YES", "NO"] = Field(
        description="What the button saw: YES if at least one document was attached")
    attachment_count: int | None = Field(default=None, ge=0, le=500,
                                         description="Number of files the button counted")
    requested_by: RequestedBy | None = None
    requested_at: datetime | None = None
    source: Literal["crm_button", "manual", "schedule", "api"] = "crm_button"

    @field_validator("customer_number", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return v or None

    @field_validator("requested_by", mode="before")
    @classmethod
    def _parse_requested_by(cls, v: Any) -> Any:
        """Accept an object, or a string like "1000000000000003 reviewer@example.com"."""
        if v is None or isinstance(v, dict):
            return v
        if isinstance(v, str):
            parts = v.replace("|", " ").replace(",", " ").split()
            email = next((p for p in parts if "@" in p), None)
            user_id = next((p for p in parts if p.isdigit()), None)
            return {"id": user_id, "email": email}
        return v

    @field_validator("requested_at")
    @classmethod
    def _assume_manila(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=MANILA)  # CRM users are in the Philippines
        return v


class JobCreated(BaseModel):
    job_id: uuid.UUID
    status: str
    created: bool
    message: str


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    source_field: str | None
    file_name: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    status: str
    status_detail: str | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: str
    customer_number: str | None
    reason: str
    reported_document_status: str | None
    reported_attachment_count: int | None
    requested_by_user_id: str | None
    requested_by_email: str | None
    requested_at: datetime | None
    source: str
    status: str
    documents_found: int
    warnings: list[dict] | None
    account_snapshot: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    documents: list[DocumentOut]
