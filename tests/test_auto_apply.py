"""Automatic Zoho updates: rules, shadow mode, writes, safety re-check, audit trail."""

import pytest

from app.assessment.auto_apply import decide, plan
from app.db.models import (
    AccountAlert,
    FieldUpdate,
    JobAssessment,
    JobStatus,
    VerificationJob,
)
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


def assessment(*proposals, cross=(), critical=(), recommendation="MANUAL_REVIEW",
               requirements=(), facts=None):
    return {"proposals": [dict(p) for p in proposals], "cross_document": list(cross),
            "documents": [{"file": "x", "type": "BIR_2303", "critical_issues": list(critical)}],
            "recommendation": recommendation, "requirements": list(requirements),
            "assessed_on": "2026-10-07", "rules_version": "t", "facts": facts or {}}


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

def _setup(db, settings, *proposals, account=None, **assessment_kwargs):
    fake = FakeZoho()
    fake.account.update(account or {"Tax_Identification_Number_TIN": None,
                                     "Owner_Name": "SALES REP"})
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    job = VerificationJob(account_id=ACCOUNT_ID, status=JobStatus.ASSESSED,
                          account_snapshot={**(account or {}), "Owner": {"id": "42",
                                                                         "name": "Rep"}})
    db.add(job)
    db.flush()
    db.add(JobAssessment(job_id=job.id, rules_version="t", recommendation="ACTIVE",
                         assessment=assessment(*proposals, **assessment_kwargs)))
    db.commit()
    return fake, client, job


def test_shadow_mode_writes_nothing(db, settings):
    fake, client, job = _setup(db, settings, TIN, OWNER, account={
        "Tax_Identification_Number_TIN": None, "Owner_Name": "SALES REP"})
    updates = auto_apply_job(db, job, client, "shadow", 0.95)
    assert {u.status for u in updates} == {"SHADOW"} and len(updates) == 2
    assert fake.updates == [] and fake.notes_created == [] and fake.tasks_created == []
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
    fake, client, job = _setup(db, settings, TIN, account={"Tax_Identification_Number_TIN": None})
    fake.update_error = {"code": "INVALID_DATA", "details": {"api_name": "Owner_Name"}}
    updates = auto_apply_job(db, job, client, "on", 0.95)
    assert updates[0].status == "FAILED" and "INVALID_DATA" in updates[0].detail


def test_off_mode_does_nothing(db, settings):
    fake, client, job = _setup(db, settings, TIN, account={"Tax_Identification_Number_TIN": None})
    assert auto_apply_job(db, job, client, "off", 0.95) == [] and fake.updates == []



# ---------- activation (never deactivation) and alerts ----------

GOOD_FACTS = {"owner_name": [{"confidence": 0.98}], "tin": [{"confidence": 0.95}]}
VALID_REQS = [{"document_type": "BIR_2303", "status": "VALID", "valid_until": None},
              {"document_type": "MAYORS_PERMIT", "status": "VALID",
               "valid_until": "2026-12-31"}]


def test_complete_valid_documents_activate_the_account(db, settings):
    fake, client, job = _setup(db, settings, TIN, account={
        "Tax_Identification_Number_TIN": None, "Customer_Status": "Inactive"},
        recommendation="ACTIVE", requirements=VALID_REQS, facts=GOOD_FACTS)
    updates = {u.zoho_field: (u.action, u.status, u.old_value, u.new_value)
               for u in auto_apply_job(db, job, client, "on", 0.95)}
    assert updates["Customer_Status"] == ("ACTIVATE", "APPLIED", "Inactive", "Active")
    assert fake.updates[0]["data"][0]["Customer_Status"] == "Active"
    assert fake.account["Customer_Status"] == "Active"


def test_activation_needs_confident_owner_and_tin(db, settings):
    fake, client, job = _setup(db, settings, account={"Customer_Status": "Inactive"},
                               recommendation="ACTIVE", requirements=VALID_REQS,
                               facts={"owner_name": [{"confidence": 0.8}],
                                      "tin": [{"confidence": 0.95}]})
    auto_apply_job(db, job, client, "on", 0.95)
    assert fake.updates == []
    decision = db.get(JobAssessment, job.id).assessment["auto_apply"]["status_decision"]
    assert decision["decision"] == "NO_CHANGE" and "owner_name" in decision["reason"]


def test_never_deactivates_and_alerts_instead(db, settings):
    expired = [{"document_type": "MAYORS_PERMIT", "status": "EXPIRED",
                "valid_until": "2025-12-31"}]
    fake, client, job = _setup(db, settings, account={"Customer_Status": "Active"},
                               recommendation="INACTIVE", requirements=expired)
    auto_apply_job(db, job, client, "on", 0.95)
    assert fake.updates == []                      # status untouched
    assert fake.account.get("Customer_Status") == "Active"
    note = fake.notes_created[0]
    assert "INACTIVE" in note["Note_Title"] and "CRITICAL" in note["Note_Title"]
    assert "Mayor's / Business Permit expired on 2025-12-31 (280 days ago)" in \
        note["Note_Content"]
    assert "ACTIVE_WITH_PROBLEMS" not in note["Note_Content"]  # message, not code, is shown
    task = fake.tasks_created[0]
    assert task["Owner"] == {"id": "42"} and task["What_Id"] == {"id": ACCOUNT_ID}
    assert task["Due_Date"] == "2026-10-10" and task["Priority"] == "High"
    alert = db.query(AccountAlert).one()
    assert alert.note_status == "CREATED" and alert.task_status == "CREATED"
    assert alert.alerts[0]["code"] == "ACTIVE_WITH_PROBLEMS"


def test_missing_documents_alert_and_same_issue_is_not_repeated(db, settings):
    missing = [{"document_type": "SEC_CERT", "status": "MISSING", "valid_until": None}]
    fake, client, job = _setup(db, settings, recommendation="MANUAL_REVIEW",
                               requirements=missing)
    auto_apply_job(db, job, client, "on", 0.95)
    assert "Missing required document: SEC Certificate of Registration" in \
        fake.notes_created[0]["Note_Content"]
    assert len(fake.tasks_created) == 1

    second = VerificationJob(account_id=ACCOUNT_ID, status=JobStatus.ASSESSED,
                             account_snapshot={})
    db.add(second)
    db.flush()
    db.add(JobAssessment(job_id=second.id, rules_version="t", recommendation="MANUAL_REVIEW",
                         assessment=assessment(recommendation="MANUAL_REVIEW",
                                               requirements=missing)))
    db.commit()
    auto_apply_job(db, second, client, "on", 0.95)
    assert len(fake.notes_created) == 2 and len(fake.tasks_created) == 1  # no duplicate task


def test_expiring_soon_is_a_warning():
    from datetime import date

    from app.assessment.alerts import build_alerts
    a = assessment(recommendation="ACTIVE", requirements=[
        {"document_type": "MAYORS_PERMIT", "status": "VALID", "valid_until": "2026-10-30"}])
    alerts = build_alerts(a, date(2026, 10, 7))
    assert alerts[0]["code"] == "EXPIRING_SOON" and "in 23 days" in alerts[0]["message"]


def test_shadow_mode_records_alert_without_zoho_calls(db, settings):
    missing = [{"document_type": "BIR_2303", "status": "MISSING", "valid_until": None}]
    fake, client, job = _setup(db, settings, recommendation="MANUAL_REVIEW",
                               requirements=missing)
    auto_apply_job(db, job, client, "shadow", 0.95)
    assert fake.notes_created == [] and fake.tasks_created == []
    alert = db.query(AccountAlert).one()
    assert (alert.note_status, alert.task_status) == ("SHADOW", "SHADOW")
