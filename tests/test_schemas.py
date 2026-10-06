import pytest
from pydantic import ValidationError

from app.schemas import JobCreate

BASE = {"account_id": "1000000000000001", "document_status": "YES"}


def test_requested_by_as_object():
    j = JobCreate(**BASE, requested_by={"id": "1000000000000003",
                                        "email": "reviewer@example.com"})
    assert j.requested_by.id == "1000000000000003"


def test_requested_by_as_combined_string():
    j = JobCreate(**BASE, requested_by="1000000000000003 reviewer@example.com")
    assert (j.requested_by.id, j.requested_by.email) == (
        "1000000000000003", "reviewer@example.com")


def test_naive_requested_at_is_manila_time():
    j = JobCreate(**BASE, requested_at="2026-10-05T15:36:00")
    assert j.requested_at.utcoffset().total_seconds() == 8 * 3600


def test_blank_reason_and_customer_number():
    j = JobCreate(**BASE, reason="   ", customer_number="")
    assert j.reason == "" and j.customer_number is None


def test_document_status_required():
    with pytest.raises(ValidationError):
        JobCreate(account_id="1000000000000001")


def test_rejects_unknown_document_status():
    with pytest.raises(ValidationError):
        JobCreate(**{**BASE, "document_status": "MAYBE"})
