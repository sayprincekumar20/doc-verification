"""Phase 3 write-back: approved review rows -> Account, with safety checks."""

import json

import httpx
import pytest

from app.review.apply import apply_review
from app.tools import apply_reviews, zoho_auth
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient

ACCOUNT = "4776053000067384366"


def review(**overrides):
    rows = [
        {"id": "r1", "Zoho_Field": "Owner_Name", "Current_Value": "MARK FERNAN PEDRON",
         "Proposed_Value": "MARICHELLE FLORES", "Action": "CORRECT", "Decision": "Approve"},
        {"id": "r2", "Zoho_Field": "Tax_Identification_Number_TIN", "Current_Value": None,
         "Proposed_Value": "601-088-612-00000", "Action": "FILL", "Decision": "Approve"},
        {"id": "r3", "Zoho_Field": "Business_Style", "Current_Value": None,
         "Proposed_Value": "RETAIL MEAT SHOP", "Action": "FILL", "Decision": "Edit"},
        {"id": "r4", "Zoho_Field": "Type_of_Business_Organization", "Current_Value": None,
         "Proposed_Value": "Single Proprietorship", "Action": "FILL", "Decision": "Reject"},
        {"id": "r5", "Zoho_Field": "Invoice_Company_Name", "Current_Value": "WINNER MEAT SHOP",
         "Action": "MATCH"},
        {"id": "r6", "Zoho_Field": "BIR_Registration_COR", "Proposed_Value": "IMG.jpg",
         "Action": "ATTACH", "Decision": "Approve"},
        {"id": "r7", "Zoho_Field": "Business_Style", "Action": "FILL", "Decision": "Pending",
         "Proposed_Value": "X"},
    ]
    base = {"id": "dv3", "Name": "DV3", "Review_Status": "Approved",
            "Account": {"id": ACCOUNT, "name": "TEST HQ A"}, "Proposed_Changes": rows}
    return {**base, **overrides}


class FakeZoho:
    def __init__(self, account=None, record=None):
        self.account = account or {"id": ACCOUNT, "Owner_Name": "MARK FERNAN PEDRON",
                                   "Tax_Identification_Number_TIN": None,
                                   "Business_Style": None}
        self.record = record or review()
        self.puts: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.method == "PUT":
            body = json.loads(request.content)
            self.puts.append((path.rsplit("/", 1)[-1], body))
            return httpx.Response(200, json={"data": [{"code": "SUCCESS", "details": {}}]})
        if path.endswith(f"/Accounts/{ACCOUNT}"):
            return httpx.Response(200, json={"data": [self.account]})
        if path.endswith("/Document_Verifications/search"):
            assert request.url.params["criteria"] == "(Review_Status:equals:Approved)"
            return httpx.Response(200, json={"data": [{"id": "dv3"}],
                                             "info": {"more_records": False}})
        if path.endswith("/Document_Verifications/dv3"):
            return httpx.Response(200, json={"data": [self.record]})
        return httpx.Response(404)

    def client(self, settings):
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                          sleep=lambda _: None)


def outcomes(result):
    return {r.row_id: r.outcome for r in result.rows}


def test_only_approved_and_edited_rows_are_written(settings):
    fake = FakeZoho()
    r = apply_review(fake.client(settings), fake.record, dry_run=False, triggers=[])
    assert outcomes(r) == {"r1": "APPLIED", "r2": "APPLIED", "r3": "APPLIED",
                           "r4": "NOT_APPROVED", "r5": "NOT_APPROVED", "r6": "UNSUPPORTED",
                           "r7": "NOT_APPROVED"}
    account_put = next(b for name, b in fake.puts if name == "Accounts")
    assert account_put["data"][0] == {"id": ACCOUNT, "Owner_Name": "MARICHELLE FLORES",
                                      "Tax_Identification_Number_TIN": "601-088-612-00000",
                                      "Business_Style": "RETAIL MEAT SHOP"}  # edited value
    assert account_put["trigger"] == []
    assert r.status == "Applied"


def test_review_record_updated_without_losing_rows(settings):
    fake = FakeZoho()
    apply_review(fake.client(settings), fake.record, dry_run=False)
    rec = next(b for name, b in fake.puts if name == "Document_Verifications")["data"][0]
    assert rec["Review_Status"] == "Applied" and rec["Reviewed_Time"]
    rows = {x["id"]: x["Auto_Applied"] for x in rec["Proposed_Changes"]}
    assert set(rows) == {"r1", "r2", "r3", "r4", "r5", "r6", "r7"}  # every row kept
    assert rows["r1"] == "Applied" and rows["r6"] == "Skipped" and rows["r4"] == "No"
    assert "BIR_Registration_COR: UNSUPPORTED" in rec["Reviewer_Notes"]


def test_field_changed_meanwhile_is_a_conflict(settings):
    fake = FakeZoho(account={"id": ACCOUNT, "Owner_Name": "SOMEONE ELSE",
                             "Tax_Identification_Number_TIN": None, "Business_Style": None})
    r = apply_review(fake.client(settings), fake.record, dry_run=False)
    assert outcomes(r)["r1"] == "CONFLICT" and outcomes(r)["r2"] == "APPLIED"
    assert r.status == "Conflict"
    account_put = next(b for name, b in fake.puts if name == "Accounts")
    assert "Owner_Name" not in account_put["data"][0]


def test_dry_run_writes_nothing(settings):
    fake = FakeZoho()
    r = apply_review(fake.client(settings), fake.record, dry_run=True)
    assert fake.puts == [] and r.status is None
    assert outcomes(r)["r1"] == "WRITE"


@pytest.fixture
def env(tmp_path):
    path = tmp_path / ".env.sandbox"
    path.write_text("ZOHO_CLIENT_ID=a\nZOHO_CLIENT_SECRET=b\nZOHO_REFRESH_TOKEN=c\n"
                    "ZOHO_API_DOMAIN=https://sandbox.zohoapis.com\n", encoding="utf-8")
    return path


def test_command_finds_approved_reviews_and_applies(env, monkeypatch, capsys):
    fake = FakeZoho()
    real = zoho_auth.build_client
    monkeypatch.setattr(apply_reviews, "build_client",
                        lambda s: real(s, httpx.Client(transport=httpx.MockTransport(
                            fake.handler))))
    assert apply_reviews.main(["--env", str(env)]) == 0
    out = capsys.readouterr().out
    assert "1 approved review(s); mode: DRY RUN" in out and fake.puts == []
    assert "WRITE        Owner_Name" in out and "-> MARICHELLE FLORES" in out
    assert apply_reviews.main(["--env", str(env), "--apply"]) == 0
    assert "Review Status -> Applied" in capsys.readouterr().out


def test_command_refuses_production(env, capsys):
    env.write_text(env.read_text(encoding="utf-8").replace("sandbox.", "www."),
                   encoding="utf-8")
    assert apply_reviews.main(["--env", str(env), "--apply"]) == 2
