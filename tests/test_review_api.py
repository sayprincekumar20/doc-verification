"""Accounts-dashboard review API: list, start, decide rows, approve / reject, page images."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api import reviews as reviews_api
from app.assessment.alerts import build_alerts
from app.assessment.assess import assess
from app.config import get_settings
from app.db.models import JobAssessment, JobStatus, Review, VerificationJob
from app.db.session import get_db
from app.main import create_app
from app.review.engine import create_engine_review
from app.storage.base import build_storage
from tests.test_assessment import SNAPSHOT, bir, dti, permit

KEY = {"X-API-Key": "test-key"}
WHO = {**KEY, "X-Reviewer": "ana@rareglobalfood.com"}
ACC = "5906238000035917292"


class FakeZoho:
    def __init__(self, account):
        self.account, self.writes = account, []

    def get_record(self, module, rid):
        return dict(self.account)

    def update_record(self, module, rid, data, triggers=None):
        self.writes.append(data)
        self.account.update(data)
        return {"code": "SUCCESS"}


@pytest.fixture
def setup(db, settings, monkeypatch):
    job = VerificationJob(account_id=ACC, status=JobStatus.ASSESSED, reason="for activation",
                          requested_by_email="rep@example.com",
                          account_snapshot={**SNAPSHOT, "Account_Name": "JUAN MEAT SHOP"})
    db.add(job)
    db.flush()
    a = assess({**SNAPSHOT, "_contacts": []}, [bir(), dti(), permit()], date(2026, 10, 28))
    a["alerts"] = build_alerts(a, date(2026, 10, 28))
    db.add(JobAssessment(job_id=job.id, rules_version="rules-1.2",
                         recommendation=a["recommendation"], assessment=a))
    db.commit()
    storage = build_storage(settings)
    review = create_engine_review(db, job, storage)
    storage.put(f"reviews/{review.id}/01.jpg", b"\xff\xd8jpeg", "image/jpeg")
    review.pages = [{"n": 1, "file": "2303.jpg", "page": 1, "key": f"reviews/{review.id}/01.jpg"}]
    db.commit()
    zoho = FakeZoho({"Owner_Name": "SALES REP NAME", "Tax_Identification_Number_TIN": None,
                     "Customer_Status": None})
    monkeypatch.setattr(reviews_api, "get_zoho_client", lambda: zoho)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), review, zoho


def test_full_dashboard_flow(setup, db):
    api, review, zoho = setup
    listed = api.get("/v1/reviews?status=open", headers=KEY).json()["items"]
    assert [x["number"] for x in listed] == [f"DV-{review.number}"]
    assert listed[0]["status"] == "Pending Review" and listed[0]["checks_met"]

    detail = api.get(f"/v1/reviews/{review.id}", headers=KEY).json()
    assert "Activation checks" in detail["summary"]["recommendation_reasons"]
    assert detail["pages"] == [
        {"n": 1, "file": "2303.jpg", "page": 1}]
    rows = {r["zoho_field"]: r for r in detail["rows"]}
    tin, owner = rows["Tax_Identification_Number_TIN"], rows["Owner_Name"]

    assert api.post(f"/v1/reviews/{review.id}/start", headers=WHO).json()["status"] == \
        "In Review"
    r = api.put(f"/v1/reviews/{review.id}/rows/{tin['id']}", headers=WHO,
                json={"decision": "Approve"})
    assert r.status_code == 200 and r.json()["decision"] == "Approve"
    bad = api.put(f"/v1/reviews/{review.id}/rows/{owner['id']}", headers=WHO,
                  json={"decision": "Edit"})
    assert bad.status_code == 409 and "corrected value" in bad.json()["detail"]
    api.put(f"/v1/reviews/{review.id}/rows/{owner['id']}", headers=WHO,
            json={"decision": "Edit", "value": "JUAN DELA CRUZ"})

    no_tick = api.post(f"/v1/reviews/{review.id}/approve", headers=WHO,
                       json={"values_compared": False})
    assert no_tick.status_code == 409
    ok = api.post(f"/v1/reviews/{review.id}/approve", headers=WHO,
                  json={"values_compared": True, "activate_account": True, "notes": "ok"})
    body = ok.json()
    assert ok.status_code == 200 and body["status"] == "Applied" and body["activated"] is True
    assert zoho.writes[0] == {"Tax_Identification_Number_TIN": tin["proposed_value"],
                              "Owner_Name": "JUAN DELA CRUZ"}
    assert zoho.writes[1] == {"Customer_Status": "Active"}
    saved = db.get(Review, review.id)
    assert saved.reviewer == "ana@rareglobalfood.com" and saved.closed_at is not None

    locked = api.put(f"/v1/reviews/{review.id}/rows/{tin['id']}", headers=WHO,
                     json={"decision": "Reject"})
    assert locked.status_code == 409 and "can no longer change" in locked.json()["detail"]


def test_reject_needs_notes_and_reviewer(setup):
    api, review, _ = setup
    assert api.post(f"/v1/reviews/{review.id}/reject", headers=KEY,
                    json={"notes": "wrong customer"}).status_code == 422      # no X-Reviewer
    assert api.post(f"/v1/reviews/{review.id}/reject", headers=WHO,
                    json={"notes": "  "}).status_code == 409
    r = api.post(f"/v1/reviews/{review.id}/request-documents", headers=WHO,
                 json={"notes": "Please upload the SEC certificate"})
    assert r.status_code == 200 and r.json()["status"] == "Waiting for Documents"


def test_page_image_and_api_key(setup):
    api, review, _ = setup
    img = api.get(f"/v1/reviews/{review.id}/pages/1", headers=KEY)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert api.get(f"/v1/reviews/{review.id}/pages/9", headers=KEY).status_code == 404
    assert api.get("/v1/reviews", headers={"X-API-Key": "wrong"}).status_code == 401
