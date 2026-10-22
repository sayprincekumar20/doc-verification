"""Scheduler: reviews approved in Zoho are applied automatically and audited."""

import uuid

from app.db.models import AuditEvent, FieldUpdate, JobStatus, VerificationJob
from app.services.review_applier import apply_approved
from tests.test_apply_reviews import ACCOUNT, FakeZoho, review


def test_approved_review_is_applied_and_recorded_on_its_job(db, settings):
    job = VerificationJob(account_id=ACCOUNT, status=JobStatus.ASSESSED)
    db.add(job)
    db.commit()
    fake = FakeZoho(record=review(Job_ID=str(job.id)))
    results = apply_approved(db, fake.client(settings), settings)
    assert len(results) == 1 and results[0].status == "Applied"
    assert any(name == "Accounts" for name, _ in fake.puts)            # Account written
    updates = {u.zoho_field: u.status for u in db.query(FieldUpdate).all()}
    assert updates == {"Owner_Name": "APPLIED", "Tax_Identification_Number_TIN": "APPLIED",
                       "Business_Style": "APPLIED"}
    event = db.query(AuditEvent).filter_by(event_type="REVIEW_APPLIED").one()
    assert event.job_id == job.id and event.payload["status"] == "Applied"


def test_offline_test_record_is_applied_without_a_job(db, settings):
    fake = FakeZoho(record=review(Job_ID="offline:5906238000061698030"))
    results = apply_approved(db, fake.client(settings), settings)
    assert results[0].status == "Applied"
    assert db.query(FieldUpdate).count() == 0
    assert db.query(AuditEvent).filter_by(event_type="REVIEW_APPLIED").one().job_id is None


def test_unknown_job_id_is_tolerated(db, settings):
    fake = FakeZoho(record=review(Job_ID=str(uuid.uuid4())))
    assert apply_approved(db, fake.client(settings), settings)[0].status == "Applied"
