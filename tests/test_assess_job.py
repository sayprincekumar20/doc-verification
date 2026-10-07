"""Phase 1D job step + API endpoint + assess_account tool."""

import hashlib
import json
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_enqueuer
from app.db.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    JobAssessment,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.db.session import get_db
from app.main import create_app
from app.pipeline.assess import assess_job
from app.pipeline.extract import extract_job, extraction_version
from app.pipeline.read import read_job
from app.storage.base import LocalStorage, storage_key_for
from app.tools import assess_account
from tests import synthetic_docs as sd
from tests.fake_vision import FakeVision

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="Tesseract not installed")
VALUES = {"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
          "bn_number": "1234567", "valid_from": "11 May 2021", "valid_to": "11 May 2026"}


def _assessed_job(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    data = sd.to_bytes(sd.page_image())
    sha = hashlib.sha256(data).hexdigest()
    key = storage_key_for(sha, "png")
    storage.put(key, data, "image/png")
    db.add(StoredFile(sha256=sha, size_bytes=len(data), mime_type="image/png", extension="png",
                      storage_key=key))
    job = VerificationJob(account_id="1000000000000001", status=JobStatus.COLLECTED,
                          account_snapshot={"Account_Name": "EXAMPLE MARKET",
                                            "Owner_Name": "SALES REP",
                                            "Owner": {"name": "SALES REP"}})
    db.add(job)
    db.flush()
    db.add(Document(job_id=job.id, source=DocumentSource.ATTACHMENT, zoho_file_ref="1",
                    file_name="dti.png", sha256=sha, status=DocumentStatus.STORED))
    db.add(Document(job_id=job.id, source=DocumentSource.ATTACHMENT, zoho_file_ref="2",
                    file_name="setup.exe", status=DocumentStatus.REJECTED,
                    status_detail="Unsupported file type"))
    db.commit()
    read_job(db, job, storage)
    fake = FakeVision(VALUES, "DTI_BN_CERT")
    extract_job(db, job, storage, fake, today=date(2026, 10, 6))
    assess_job(db, job, extraction_version(fake), date(2026, 10, 6))
    return job


def test_assess_job_stores_recommendation(db, settings):
    job = _assessed_job(db, settings)
    assert job.status == JobStatus.ASSESSED and job.completed_at is not None
    row = db.get(JobAssessment, job.id)
    a = row.assessment
    assert row.recommendation == "MANUAL_REVIEW"  # only a DTI: BIR 2303 + permit missing
    assert any("missing: BIR_2303" in r for r in a["reasons"])
    assert a["documents"][0]["validity"] == "EXPIRED"
    assert {p["zoho_field"]: p["action"] for p in a["proposals"]}["Owner_Name"] == "CORRECT"
    assert a["skipped_documents"][0]["file"] == "setup.exe"


def test_assessment_endpoint(db, settings):
    job = _assessed_job(db, settings)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_enqueuer] = lambda: (lambda _id: None)
    api = TestClient(app)
    resp = api.get(f"/v1/jobs/{job.id}/assessment", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200 and resp.json()["recommendation"] == "MANUAL_REVIEW"
    missing = api.get("/v1/jobs/00000000-0000-0000-0000-000000000000/assessment",
                      headers={"X-API-Key": "test-key"})
    assert missing.status_code == 404


def test_assess_account_tool(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "1000000000000001"
    (folder / "files").mkdir(parents=True)
    (folder / "files" / "attachment_1_dti.pdf").write_bytes(sd.digital_pdf())
    (folder / "account.json").write_text(json.dumps(
        {"id": "1000000000000001", "Account_Name": "EXAMPLE MARKET", "Owner_Name": None}),
        encoding="utf-8")
    monkeypatch.setattr(assess_account, "build_provider",
                        lambda *a, **k: FakeVision(VALUES, "DTI_BN_CERT"))
    rc = assess_account.main([str(folder), "--env", str(tmp_path / "x.env"),
                              "--today", "2026-10-06", "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0 and "RECOMMENDATION: MANUAL_REVIEW" in out
    assert "FILL" in out and "Owner_Name" in out and "JUAN DELA CRUZ" in out
    saved = json.loads((folder / "assessment.json").read_text(encoding="utf-8"))
    assert saved["assessment"]["recommendation"] == "MANUAL_REVIEW"
