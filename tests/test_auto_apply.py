"""Automatic Zoho updates: rules, shadow mode, writes, safety re-check, audit trail."""

import pytest

from app.assessment.auto_apply import decide, plan
from app.db.models import FieldUpdate, JobAssessment, JobStatus, VerificationJob
from app.pipeline.auto_apply import auto_apply_job
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from tests.fakes import ACCOUNT_ID, FakeZoho

TIN = {"zoho_field": "Tax_Identification_Number_TIN", "action": "FILL",
       "current_value": None, "proposed_value": "123-456-789-00000", "confidence": 0.95,
       "grounding": "EXACT", "agreeing_documents": 1, "needs_attention": False,
       "sources": [{"document": "bir.jpg"}]}
OWNER = {"zoho_field": "Owner_Name", "action": "CORRECT", "current_value": "SALES REP",
         "proposed_value": "JUAN DELA CRUZ", "confidence": 0.98, "grounding": "EXACT",
         "agreeing_documents": 3, "needs_attention": False, "sources": []}


def assessment(*proposals, cross=(), critical=()):
    return {"proposals": [dict(p) for p in proposals], "cross_document": list(cross),
            "documents": [{"file": "x", "critical_issues": list(critical)}]}


@pytest.mark.parametrize("change,auto,why", [
    ({}, True, "EXACT"),
    ({"confidence": 0.94}, False, "below threshold"),
    ({"grounding": "NOT_FOUND"}, False, "not confirmed"),
    ({"grounding": "FUZZY"}, False, "not confirmed"),
    ({"needs_attention": True}, False, "needs attention"),
    ({"zoho_field": "Billing_Street"}, False, "always goes to review"),
    ({"action": "CORRECT", "current_value": "111-111-111-00000"}, False, "2 agreeing"),
])
def test_decide_rules(change, auto, why):
    ok, reason = decide({**TIN, **change}, 0.95, [])
    assert ok is auto and why in reason


def test_correct_with_three_agreeing_documents_is_auto():
    assert decide(OWNER, 0.95, [])[0]


def test_critical_problems_block_everything_but_expiry_does_not():
    blocked = plan(assessment(TIN, critical=["BRANCH_MISMATCH"]), 0.95)
    assert blocked[0]["decision"] == "REVIEW" and "BRANCH_MISMATCH" in blocked[0]["reason"]
    expired_only = plan(assessment(TIN, critical=["EXPIRED"]), 0.95)
    assert expired_only[0]["decision"] == "AUTO"
    conflict = plan(assessment(TIN, cross=[{"attribute": "owner_name", "status": "CONFLICT",
                                            "severity": "CRITICAL"}]), 0.95)
    assert conflict[0]["decision"] == "REVIEW"


def test_hold_and_match_are_never_planned():
    held = {**TIN, "action": "HOLD"}
    match = {**TIN, "action": "MATCH"}
    assert plan(assessment(held, match), 0.95) == []


# ---------- execution against a fake Zoho ----------

def _setup(db, settings, *proposals, account=None):
    fake = FakeZoho()
    fake.account.update(account or {"Tax_Identification_Number_TIN": None,
                                     "Owner_Name": "SALES REP"})
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    job = VerificationJob(account_id=ACCOUNT_ID, status=JobStatus.ASSESSED)
    db.add(job)
    db.flush()
    db.add(JobAssessment(job_id=job.id, rules_version="t", recommendation="ACTIVE",
                         assessment=assessment(*proposals)))
    db.commit()
    return fake, client, job


def test_shadow_mode_writes_nothing(db, settings):
    fake, client, job = _setup(db, settings, TIN, OWNER)
    updates = auto_apply_job(db, job, client, "shadow", 0.95)
    assert {u.status for u in updates} == {"SHADOW"} and len(updates) == 2
    assert fake.updates == []
    a = db.get(JobAssessment, job.id).assessment
    assert a["auto_apply"]["mode"] == "shadow"
    assert {p["auto_apply_status"] for p in a["proposals"]} == {"SHADOW"}


def test_on_mode_writes_qualifying_fields_once(db, settings):
    low = {**TIN, "zoho_field": "Business_Style", "proposed_value": "RETAIL", "confidence": 0.8}
    fake, client, job = _setup(db, settings, TIN, OWNER, low, account={
        "Tax_Identification_Number_TIN": None, "Owner_Name": "SALES REP",
        "Business_Style": None})
    updates = auto_apply_job(db, job, client, "on", 0.95, triggers=[])
    assert len(fake.updates) == 1
    body = fake.updates[0]
    assert body["trigger"] == []
    assert body["data"][0] == {"id": ACCOUNT_ID, "Tax_Identification_Number_TIN":
                               "123-456-789-00000", "Owner_Name": "JUAN DELA CRUZ"}
    assert {u.zoho_field: u.status for u in updates} == {
        "Tax_Identification_Number_TIN": "APPLIED", "Owner_Name": "APPLIED"}
    owner = db.query(FieldUpdate).filter_by(zoho_field="Owner_Name").one()
    assert owner.old_value == "SALES REP" and owner.new_value == "JUAN DELA CRUZ"


def test_field_changed_in_zoho_meanwhile_is_skipped(db, settings):
    fake, client, job = _setup(db, settings, TIN, OWNER, account={
        "Tax_Identification_Number_TIN": "999-999-999-00000",  # someone filled it meanwhile
        "Owner_Name": "SALES REP"})
    updates = {u.zoho_field: u.status for u in auto_apply_job(db, job, client, "on", 0.95)}
    assert updates == {"Tax_Identification_Number_TIN": "SKIPPED_CHANGED",
                       "Owner_Name": "APPLIED"}
    assert "Tax_Identification_Number_TIN" not in fake.updates[0]["data"][0]


def test_zoho_rejection_is_recorded_as_failed(db, settings):
    fake, client, job = _setup(db, settings, TIN)
    fake.update_error = {"code": "INVALID_DATA", "details": {"api_name": "Owner_Name"}}
    updates = auto_apply_job(db, job, client, "on", 0.95)
    assert updates[0].status == "FAILED" and "INVALID_DATA" in updates[0].detail


def test_off_mode_does_nothing(db, settings):
    fake, client, job = _setup(db, settings, TIN)
    assert auto_apply_job(db, job, client, "off", 0.95) == [] and fake.updates == []
