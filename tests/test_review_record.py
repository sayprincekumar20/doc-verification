"""Phase 3: Zoho review record built from an assessment, created once per job, images
attached; and the sandbox test command."""

import hashlib
import json
import shutil
from datetime import date

import pytest

from app.assessment.assess import assess
from app.db.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    JobAssessment,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.pipeline.assess import assess_job
from app.pipeline.extract import extract_job, extraction_version
from app.pipeline.read import read_job
from app.pipeline.review_record import create_review_record
from app.storage.base import LocalStorage, storage_key_for
from app.tools import create_review_record as tool
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from app.zoho.review_record import build_record, review_status
from tests import synthetic_docs as sd
from tests.fake_vision import FakeVision
from tests.fakes import ACCOUNT_ID, FakeZoho
from tests.test_assessment import SNAPSHOT, bir, dti, permit

TODAY = date(2026, 10, 8)


def winner_like():
    a = assess(SNAPSHOT, [bir(), dti(until="2026-05-11", status="EXPIRED"), permit()], TODAY)
    a["alerts"] = [{"severity": "CRITICAL", "message": "DTI expired on 2026-05-11",
                    "action": "Collect the renewed DTI"}]
    a["status_decision"] = {"decision": "NO_CHANGE", "reason": "Recommendation is INACTIVE"}
    return a


def test_record_has_summary_and_one_row_per_proposal():
    a = winner_like()
    r = build_record(a, ACCOUNT_ID, job_id="job-1", requested_by="rep@example.com",
                     request_reason="for activation po Ma'am")
    assert r["Account"] == {"id": ACCOUNT_ID}
    assert r["Recommendation"] == "INACTIVE" and r["Review_Status"] == "Pending Review"
    assert "[CRITICAL] DTI expired on 2026-05-11 -> Collect the renewed DTI" in r["Alerts"]
    assert "Customer_Status: NO_CHANGE" in r["Recommendation_Reasons"]
    assert "DTI_BN_CERT: EXPIRED (until 2026-05-11)" in r["Required_Documents"]
    rows = {row["Zoho_Field"]: row for row in r["Proposed_Changes"]}
    owner = rows["Owner_Name"]
    assert owner["Action"] == "CORRECT" and owner["Decision"] == "Pending"
    assert owner["Current_Value"] == "SALES REP NAME"
    assert owner["Proposed_Value"] == "JUAN DELA CRUZ"
    assert owner["Field_Label"] == "Owner Name" and owner["OCR_Check"] == "EXACT"
    assert "DTI_BN_CERT dti.jpg: JUAN DELA CRUZ" in owner["Evidence"]
    assert "Decision" not in rows["Invoice_Company_Name"]  # MATCH: context only
    assert r["Proposed_Changes"][0]["Action"] != "MATCH"   # rows needing decisions first
    assert r["Lowest_Confidence"] == min(
        p["confidence"] for p in a["proposals"] if p["action"] in ("FILL", "CORRECT"))


def test_auto_applied_rows_need_no_decision_and_status_follows():
    a = winner_like()
    for p in a["proposals"]:
        if p["action"] in ("FILL", "CORRECT", "ATTACH"):
            p["auto_apply_status"] = "APPLIED"
    a["proposals"] = [p for p in a["proposals"] if p["action"] not in (
        "DIFFERS", "HOLD", "REVIEW_CONFLICT", "NO_PICKLIST_VALUE")]
    rows = build_record(a, ACCOUNT_ID)["Proposed_Changes"]
    assert all("Decision" not in r for r in rows)
    assert {r["Auto_Applied"] for r in rows if r["Action"] == "FILL"} == {"Applied"}
    assert review_status(a) == "Waiting for Documents"  # DTI expired


def test_long_values_are_truncated_for_zoho():
    a = winner_like()
    a["reasons"] = ["x" * 5000]
    r = build_record(a, ACCOUNT_ID)
    assert len(r["Recommendation_Reasons"]) <= 2000


# ---------- pipeline step ----------

@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_pipeline_creates_one_record_with_page_images(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    data = sd.to_bytes(sd.page_image())
    sha = hashlib.sha256(data).hexdigest()
    storage.put(storage_key_for(sha, "png"), data, "image/png")
    db.add(StoredFile(sha256=sha, size_bytes=len(data), mime_type="image/png", extension="png",
                      storage_key=storage_key_for(sha, "png")))
    job = VerificationJob(account_id=ACCOUNT_ID, status=JobStatus.COLLECTED,
                          requested_by_email="rep@example.com", reason="please check",
                          account_snapshot={"Account_Name": "EXAMPLE MARKET"})
    db.add(job)
    db.flush()
    db.add(Document(job_id=job.id, source=DocumentSource.ATTACHMENT, zoho_file_ref="1",
                    file_name="dti.png", sha256=sha, status=DocumentStatus.STORED))
    db.commit()
    read_job(db, job, storage)
    fake_ai = FakeVision({"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
                          "bn_number": "1234567", "valid_from": "11 May 2021",
                          "valid_to": "11 May 2026"}, "DTI_BN_CERT")
    extract_job(db, job, storage, fake_ai, today=TODAY)
    assess_job(db, job, extraction_version(fake_ai), TODAY)

    zoho = FakeZoho()
    http = zoho.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    review_id = create_review_record(db, job, client, storage)
    assert review_id == "dv1"
    record = zoho.records_created[0]
    assert record["Requested_By"] == "rep@example.com"
    assert record["Request_Reason"] == "please check" and record["Job_ID"] == str(job.id)
    assert zoho.attachments_uploaded[0][:2] == ("dv1", "dti.png - page 1.jpg")
    assert db.get(JobAssessment, job.id).zoho_review_id == "dv1"

    assert create_review_record(db, job, client, storage) == "dv1"  # no second record
    assert len(zoho.records_created) == 1


def test_pipeline_records_zoho_error(db, settings):
    job = VerificationJob(account_id=ACCOUNT_ID, status=JobStatus.ASSESSED)
    db.add(job)
    db.flush()
    db.add(JobAssessment(job_id=job.id, rules_version="t", recommendation="INACTIVE",
                         assessment=winner_like()))
    db.commit()
    zoho = FakeZoho()
    zoho.record_error = {"code": "INVALID_DATA", "details": {"api_name": "Account"}}
    http = zoho.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    assert create_review_record(db, job, client, LocalStorage(settings.storage_local_dir)) is None
    assert "INVALID_DATA" in db.get(JobAssessment, job.id).review_error


# ---------- sandbox test command ----------

def _folder(tmp_path):
    folder = tmp_path / ACCOUNT_ID
    (folder / "files").mkdir(parents=True)
    (folder / "files" / "attachment_1_dti.jpg").write_bytes(b"\xff\xd8\xff jpeg")
    (folder / "assessment.json").write_text(json.dumps({"assessment": winner_like()}),
                                            encoding="utf-8")
    env = tmp_path / ".env.sandbox"
    env.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n"
                   "ZOHO_API_DOMAIN=https://sandbox.zohoapis.com\n", encoding="utf-8")
    return folder, env


def test_command_dry_run_and_apply(tmp_path, monkeypatch, capsys):
    folder, env = _folder(tmp_path)
    assert tool.main([str(folder), "--env", str(env), "--account-id", ACCOUNT_ID]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "CORRECT" in out and "Owner_Name" in out

    zoho = FakeZoho()
    from app.tools import zoho_auth
    real = zoho_auth.build_client
    monkeypatch.setattr(tool, "build_client", lambda s: real(s, zoho.http()))
    assert tool.main([str(folder), "--env", str(env), "--account-id", ACCOUNT_ID,
                      "--apply"]) == 0
    out = capsys.readouterr().out
    assert "OK  created review record dv1" in out and "attached attachment_1_dti.jpg" in out
    assert zoho.records_created[0]["Account"] == {"id": ACCOUNT_ID}


def test_command_refuses_production(tmp_path, capsys):
    folder, env = _folder(tmp_path)
    env.write_text(env.read_text(encoding="utf-8").replace("sandbox.zohoapis", "www.zohoapis"),
                   encoding="utf-8")
    assert tool.main([str(folder), "--env", str(env), "--apply"]) == 2
