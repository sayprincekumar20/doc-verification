"""Cliq messages + Google Sheet rows: verification, approved review, expiry reminders."""

import json
from datetime import date, datetime, timedelta

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.db.models import FieldUpdate, JobAssessment, JobStatus, VerificationJob
from app.notify.cliq import CliqClient
from app.notify.events import check_expiry, notify_applied_reviews, notify_verification
from app.notify.notifier import MANILA, Notifier
from app.notify.sheets import SheetsClient

ACC = "5906238000035917292"
TODAY = date(2026, 11, 2)


class Recorder:
    def __init__(self):
        self.messages, self.rows = [], []

    def post(self, text):
        self.messages.append(text)
        return True

    def ensure_tabs(self, tabs):
        pass

    def append(self, tab, rows):
        self.rows += [(tab, r) for r in rows]
        return True


def notifier():
    rec = Recorder()
    return Notifier(rec, rec), rec


def _job(db, until="2026-12-31", status="VALID", created=None):
    job = VerificationJob(account_id=ACC, status=JobStatus.ASSESSED, reason="for activation",
                          requested_by_email="rep@example.com",
                          account_snapshot={"Account_Name": "EXAMPLE CAFE INC.",
                                            "Inventory_Customer_Number": "RGFC-00001"})
    db.add(job)
    db.flush()
    a = {"recommendation": "ACTIVE", "documents": [{}, {}],
         "requirements": [{"document_type": "BIR_2303", "status": "NO_EXPIRY", "file": "2303.pdf",
                           "valid_until": None},
                          {"document_type": "MAYORS_PERMIT", "status": status,
                           "file": "permit.jpg", "valid_until": until}],
         "activation_checks": [{"key": "payment_terms", "label": "COD", "status": "PASS"},
                               {"key": "tin", "label": "TIN", "status": "PASS"}]}
    db.add(JobAssessment(job_id=job.id, rules_version="rules-1.2", recommendation="ACTIVE",
                         assessment=a, zoho_review_id=None,
                         **({"created_at": created} if created else {})))
    db.commit()
    return job


def test_verification_message_and_rows_once(db):
    job = _job(db)
    db.add(FieldUpdate(job_id=job.id, account_id=ACC, zoho_field="Tax_Identification_Number_TIN",
                       action="FILL", old_value=None, new_value="600-111-222-00000",
                       confidence=0.98, grounding="EXACT", sources=[], mode="on",
                       status="APPLIED"))
    db.add(FieldUpdate(job_id=job.id, account_id=ACC, zoho_field="Customer_Status",
                       action="ACTIVATE", old_value=None, new_value="Active", confidence=None,
                       grounding=None, sources=[], mode="on", status="APPLIED"))
    db.commit()
    n, rec = notifier()
    notify_verification(db, job, n)
    notify_verification(db, job, n)                       # second call: nothing new
    assert len(rec.messages) == 1
    text = rec.messages[0]
    assert text.startswith("✅ Verification done: EXAMPLE CAFE INC. (RGFC-00001)")
    assert "Requested by rep@example.com · ACTIVE · activation checks 2/2" in text
    assert "Updated automatically: TIN" in text and "Customer Status → Active" in text
    tabs = [t for t, _ in rec.rows]
    assert tabs == ["Verifications", "Field Updates", "Activations"]


class FakeZoho:
    def __init__(self, reviews=None, attachments=None):
        self.reviews = reviews or {}
        self.attachments = attachments or []

    def search_records(self, module, criteria):
        status = criteria.split(":equals:")[1].rstrip(")")
        return [{"id": rid, "Modified_Time": r["Modified_Time"]}
                for rid, r in self.reviews.items() if r["Review_Status"] == status]

    def get_record(self, module, rid):
        if module == "Accounts":
            return {"Inventory_Customer_Number": "RGFC-00001"}
        return self.reviews[rid]

    def list_attachments(self, module, rid):
        return self.attachments


def _review(status="Applied", modified=None, activate=True):
    modified = modified or datetime.now(MANILA).isoformat()
    return {"Name": "DV12", "Review_Status": status, "Modified_Time": modified,
            "Account": {"id": ACC, "name": "EXAMPLE CAFE INC."},
            "Reviewer": {"name": "Prince Kumar"},
            "Reviewer_Notes": " | Write-back | Owner_Name: written"
                              + (" | Customer Status set to Active (reviewer decision)"
                                 if activate else ""),
            "Proposed_Changes": [
                {"Field_Label": "Owner Name", "Zoho_Field": "Owner_Name", "Decision": "Approve",
                 "Auto_Applied": "Applied", "Current_Value": "", "Proposed_Value": "ANA REYES",
                 "Confidence": 0.98},
                {"Field_Label": "TIN", "Zoho_Field": "Tax_Identification_Number_TIN",
                 "Decision": "Edit", "Auto_Applied": "Skipped", "Current_Value": "1",
                 "Proposed_Value": "2"},
                {"Field_Label": "Account Name", "Zoho_Field": "Account_Name",
                 "Decision": "Reject", "Auto_Applied": "No"}]}


def test_approved_review_announced_once_old_ones_silent(db):
    old = (datetime.now(MANILA) - timedelta(days=10)).isoformat()
    zoho = FakeZoho({"1": _review(), "2": _review(modified=old)})
    n, rec = notifier()
    assert notify_applied_reviews(db, zoho, n) == 1
    assert notify_applied_reviews(db, zoho, n) == 0
    assert len(rec.messages) == 1
    msg = rec.messages[0]
    assert msg.startswith("👍 DV12 approved by Prince Kumar for EXAMPLE CAFE INC.: 1 field(s) "
                          "updated (Owner Name); 1 not written")
    assert "Customer Status → Active (reviewer decision)" in msg
    fu = [r for t, r in rec.rows if t == "Field Updates"]
    assert [r[4] for r in fu] == ["Owner Name", "TIN"] and fu[1][-1] == "Conflict"
    assert [t for t, _ in rec.rows].count("Activations") == 1


class S:
    expiry_alert_days = "30,7,0"


def test_expiry_levels_sent_once(db):
    _job(db, until=(TODAY + timedelta(days=25)).isoformat())
    n, rec = notifier()
    assert check_expiry(db, FakeZoho(), n, S, lambda _: None, TODAY)["alerts"] == 1
    assert check_expiry(db, FakeZoho(), n, S, lambda _: None, TODAY)["alerts"] == 0
    assert "Mayor's permit of EXAMPLE CAFE INC. (RGFC-00001) expires on" in rec.messages[0]
    assert "(in 25 days)" in rec.messages[0]
    # 6 days left: the 7-day level is a new reminder
    assert check_expiry(db, FakeZoho(), n, S, lambda _: None,
                        TODAY + timedelta(days=19))["alerts"] == 1


def test_new_attachment_triggers_recheck_instead_of_alert(db):
    _job(db, until=(TODAY + timedelta(days=5)).isoformat(),
         created=datetime(2026, 10, 1, tzinfo=MANILA))
    zoho = FakeZoho(attachments=[{"Created_Time": "2026-11-01T10:00:00+08:00"}])
    n, rec = notifier()
    queued = []
    summary = check_expiry(db, zoho, n, S, queued.append, TODAY)
    assert summary == {"alerts": 0, "rechecks": 1} and len(queued) == 1 and rec.messages == []


def test_renewed_document_announced(db):
    _job(db, until=(TODAY + timedelta(days=3)).isoformat(),
         created=datetime(2026, 10, 1, tzinfo=MANILA))
    n, rec = notifier()
    check_expiry(db, FakeZoho(), n, S, lambda _: None, TODAY)
    renewed = _job(db, until="2027-12-31")
    notify_verification(db, renewed, n)
    assert any(m.startswith("♻️ Mayor's permit of EXAMPLE CAFE INC. renewed: valid until "
                            "31 Dec 2027") for m in rec.messages)
    assert ("Document Expiry", ) == (next(t for t, r in rec.rows if r[-1] == "Renewed"),)


def test_expired_document_alert():
    from app.notify.events import _level
    assert _level(-3, [0, 7, 30]) == "expired" and _level(0, [0, 7, 30]) == "today"
    assert _level(7, [0, 7, 30]) == "7 days" and _level(31, [0, 7, 30]) is None


def test_cliq_client_posts_text_with_webhook_token():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), body=json.loads(request.content))
        return httpx.Response(204)
    c = CliqClient("https://cliq.zoho.com/company/1/api/v2/channelsbyname/accountactivation/message",
                   "TOKEN", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.post("hello") is True
    assert seen["url"].endswith("/message?zapikey=TOKEN") and seen["body"] == {"text": "hello"}


def test_sheets_client_creates_tabs_and_appends():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.method == "GET":
            return httpx.Response(200, json={"sheets": [{"properties": {"title": "Sheet1"}}]})
        return httpx.Response(200, json={})
    s = SheetsClient("SHEET", {"client_email": "dv@x.iam.gserviceaccount.com",
                               "private_key": pem},
                     http=httpx.Client(transport=httpx.MockTransport(handler)))
    s.ensure_tabs({"Verifications": ["Date"]})
    assert s.append("Verifications", [["2026-11-02", None]]) is True
    paths = [p for _, p in calls]
    assert "/v4/spreadsheets/SHEET:batchUpdate" in paths
    assert paths[-1] == "/v4/spreadsheets/SHEET/values/'Verifications'!A1:append"
