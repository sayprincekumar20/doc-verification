"""End-to-end collection using representative Zoho response fixtures."""

from datetime import datetime

from app.db.models import DocumentSource, DocumentStatus, JobStatus, VerificationJob
from app.pipeline.collect import collect_job
from app.schemas import JobCreate
from app.services.jobs import create_or_get_active_job
from app.storage.base import LocalStorage
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import NOTE_FIELDS, ZohoClient
from tests.fakes import FakeZoho

BUTTON_PAYLOAD = {
    "account_id": "1000000000000001",
    "customer_number": "EXAMPLE-001",
    "reason": "Routine verification request",
    "document_status": "YES",
    "attachment_count": 3,
    "requested_by": {"id": "1000000000000003", "email": "reviewer@example.com"},
    "requested_at": "2026-10-05T15:36:00+08:00",
    "source": "crm_button",
}


def run_button_flow(db, settings, fake, **overrides):
    job, created = create_or_get_active_job(db, JobCreate(**{**BUTTON_PAYLOAD, **overrides}))
    assert created
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    return collect_job(db, job, client, LocalStorage(settings.storage_local_dir), settings)


def test_representative_payload_end_to_end(db, settings):
    fake = FakeZoho.from_real_responses()
    job = run_button_flow(db, settings, fake)

    # Button payload stored as sent
    assert job.reason == "Routine verification request"
    assert job.reported_attachment_count == 3
    assert job.requested_by_user_id == "1000000000000003"
    assert job.requested_by_email == "reviewer@example.com"
    assert job.requested_at == datetime.fromisoformat("2026-10-05T15:36:00+08:00")

    # Same Zoho calls as the APIs shared at the start
    assert "GET /crm/v8/Accounts/1000000000000001" in fake.calls
    assert "GET /crm/v8/Accounts/1000000000000001/Attachments" in fake.calls
    assert "GET /crm/v8/Accounts/1000000000000001/Notes" in fake.calls
    for att_id in ("1000000000000011", "1000000000000012", "1000000000000013"):
        assert f"GET /crm/v8/Accounts/1000000000000001/Attachments/{att_id}" in fake.calls
    assert {"fields": NOTE_FIELDS, "per_page": "200", "page": "1"} in fake.params

    # 3 photos stored; the note has "$attachments": null and adds nothing
    assert job.status == JobStatus.COLLECTED
    assert job.documents_found == 3
    assert {d.source for d in job.documents} == {DocumentSource.ATTACHMENT}
    assert all(d.status == DocumentStatus.STORED for d in job.documents)
    assert sorted(d.file_name for d in job.documents) == [
        "document-01.jpg", "document-02.jpg", "document-03.jpg"]
    assert all(d.zoho_uploaded_by == "reviewer@example.com" for d in job.documents)

    # Account snapshot = expected values for later phases
    snap = job.account_snapshot
    assert snap["Account_Name"] == "Example Market"
    assert snap["Customer_Status"] == "Active"
    assert snap["Billing_Street"] == "123 Example Street, Example City"
    assert snap["Tax_Identification_Number_TIN"] is None
    assert snap["Modified_Time"] == "2026-09-21T17:19:05+08:00"

    assert not job.warnings


def test_warns_when_customer_number_differs(db, settings):
    job = run_button_flow(db, settings, FakeZoho.from_real_responses(),
                          customer_number="EXAMPLE-999")
    assert [w["code"] for w in job.warnings] == ["CUSTOMER_NUMBER_MISMATCH"]


def test_warns_when_attachment_count_differs(db, settings):
    job = run_button_flow(db, settings, FakeZoho.from_real_responses(), attachment_count=5)
    w = job.warnings[0]
    assert w["code"] == "ATTACHMENT_COUNT_MISMATCH"
    assert (w["reported"], w["found_attachments"]) == (5, 3)


def test_document_status_no_but_files_exist(db, settings):
    job = run_button_flow(db, settings, FakeZoho.from_real_responses(),
                          document_status="NO", attachment_count=0)
    codes = {w["code"] for w in job.warnings}
    assert "DOCUMENT_STATUS_MISMATCH" in codes
    assert job.status == JobStatus.COLLECTED  # the engine trusts what it actually finds


def test_customer_number_filled_from_zoho_when_missing(db, settings):
    job = run_button_flow(db, settings, FakeZoho.from_real_responses(), customer_number=None)
    assert job.customer_number == "EXAMPLE-001"


def test_job_persists(db, settings):
    job = run_button_flow(db, settings, FakeZoho.from_real_responses())
    db.expire_all()
    again = db.get(VerificationJob, job.id)
    assert again.account_snapshot["Invoice_Company_Name"] == "Example Market"
