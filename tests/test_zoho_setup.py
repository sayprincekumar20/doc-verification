"""Zoho OAuth setup tools, the /health/zoho endpoint and the fileupload download path."""

import json

from fastapi.testclient import TestClient

from app.db.models import DocumentSource, DocumentStatus
from app.main import create_app
from app.tools import fetch_account, zoho_auth
from app.tools.envfile import read_env, set_env_value
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoAPIError
from app.zoho.factory import get_zoho_client
from tests.fakes import ACCOUNT_ID, JPEG, PDF, FakeZoho
from tests.test_collect import run

BASE_ENV = "ZOHO_CLIENT_ID=1000.CLIENT\nZOHO_CLIENT_SECRET=secret\n# keep me\nAPI_KEY=k\n"


def _env(tmp_path, extra=""):
    path = tmp_path / ".env"
    path.write_text(BASE_ENV + extra)
    return path


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------- .env helpers ----------

def test_set_env_value_replaces_and_appends(tmp_path):
    path = _env(tmp_path, "ZOHO_REFRESH_TOKEN=old\n")
    set_env_value(path, "ZOHO_REFRESH_TOKEN", "new")
    set_env_value(path, "ZOHO_API_DOMAIN", "https://www.zohoapis.com")
    text = path.read_text()
    assert "ZOHO_REFRESH_TOKEN=new" in text and "old" not in text
    assert "# keep me" in text and text.endswith("ZOHO_API_DOMAIN=https://www.zohoapis.com\n")
    assert read_env(path)["ZOHO_CLIENT_ID"] == "1000.CLIENT"


# ---------- exchange-code ----------

def test_exchange_code_saves_refresh_token(tmp_path, capsys):
    path = _env(tmp_path)
    fake = FakeZoho()
    rc = zoho_auth.cmd_exchange(Args(env=str(path), code="1000.goodcode"), http=fake.http())
    assert rc == 0
    assert read_env(path)["ZOHO_REFRESH_TOKEN"] == "1000.refresh.abcdefghijkl"
    out = capsys.readouterr().out
    assert "1000.refresh.abcdefghijkl" not in out  # never printed in full


def test_exchange_expired_code_explains(tmp_path):
    path = _env(tmp_path)
    try:
        zoho_auth.cmd_exchange(Args(env=str(path), code="1000.expired"), http=FakeZoho().http())
    except Exception as exc:
        assert "invalid_code" in str(exc) and "generate a new one" in str(exc)
    else:
        raise AssertionError("expected failure")
    assert "ZOHO_REFRESH_TOKEN" not in path.read_text()


# ---------- test command ----------

def test_test_command_all_checks_pass(tmp_path, capsys):
    path = _env(tmp_path, "ZOHO_REFRESH_TOKEN=1000.r\n")
    rc = zoho_auth.cmd_test(Args(env=str(path), account_id=ACCOUNT_ID),
                            http=FakeZoho.from_real_responses().http())
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "integration@example.com" in out and "3 attachment(s)" in out
    assert "All checks passed." in out


def test_test_command_reports_missing_scope(tmp_path, capsys):
    path = _env(tmp_path, "ZOHO_REFRESH_TOKEN=1000.r\n")
    fake = FakeZoho.from_real_responses()
    fake.scope_missing_paths.add(f"/crm/v8/Accounts/{ACCOUNT_ID}/Attachments")
    rc = zoho_auth.cmd_test(Args(env=str(path), account_id=ACCOUNT_ID), http=fake.http())
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL  ZohoCRM.modules.attachments.READ (Attachments): missing scope" in out


def test_scope_mismatch_does_not_waste_a_token_refresh(settings):
    fake = FakeZoho()
    fake.scope_missing_paths.add("/crm/v8/users")
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    try:
        client.get_current_user()
    except ZohoAPIError as exc:
        assert exc.status_code == 401
    assert fake.token_requests == 1


# ---------- revoke ----------

def test_revoke_clears_env(tmp_path):
    path = _env(tmp_path, "ZOHO_REFRESH_TOKEN=1000.leaked\n")
    fake = FakeZoho()
    assert zoho_auth.cmd_revoke(Args(env=str(path)), http=fake.http()) == 0
    assert fake.revoked == ["1000.leaked"]
    assert read_env(path)["ZOHO_REFRESH_TOKEN"] == ""


# ---------- fetch_account ----------

def test_fetch_account_saves_data_and_files(tmp_path):
    path = _env(tmp_path, "ZOHO_REFRESH_TOKEN=1000.r\n")
    fake = FakeZoho.from_real_responses()
    manifest = fetch_account.fetch(ACCOUNT_ID, tmp_path / "samples", path, http=fake.http())
    folder = tmp_path / "samples" / ACCOUNT_ID
    assert json.loads((folder / "account.json").read_text())["Account_Name"] == "Example Market"
    assert len(json.loads((folder / "attachments.json").read_text())) == 3
    assert [f["status"] for f in manifest["files"]] == ["OK", "OK", "OK"]
    for f in manifest["files"]:
        assert (folder / f["saved_as"]).read_bytes()[:3] == JPEG[:3]
        assert len(f["sha256"]) == 64
    # Account, Notes and Attachments list read once each (no duplicate list calls)
    assert sum(c.endswith("/Attachments") for c in fake.calls) == 1
    assert sum(c.endswith("/Notes") for c in fake.calls) == 1


# ---------- fileupload fields use download_fields_attachment ----------

def test_fileupload_field_uses_dedicated_download(db, settings):
    fake = FakeZoho()
    fake.account["Business_Permit"] = [{"attachment_Id": "777", "File_Id__s": "enc",
                                        "File_Name__s": "permit.pdf"}]
    fake.field_attachments["777"] = PDF
    job, _ = run(db, settings, fake)
    doc = job.documents[0]
    assert doc.source == DocumentSource.FILE_FIELD and doc.status == DocumentStatus.STORED
    assert any("actions/download_fields_attachment" in c for c in fake.calls)
    assert not any(c.endswith("/files") for c in fake.calls)


# ---------- /health/zoho ----------

def test_health_zoho_endpoint(settings, db):
    fake = FakeZoho()
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    app = create_app()
    app.dependency_overrides[get_zoho_client] = lambda: client
    api = TestClient(app)
    assert api.get("/health/zoho").status_code == 401  # API key required
    resp = api.get("/health/zoho", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "zoho_user": "integration@example.com",
                           "profile": "Administrator", "role": "CEO"}


def test_health_zoho_reports_missing_scope(settings):
    fake = FakeZoho()
    fake.scope_missing_paths.add("/crm/v8/users")
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    app = create_app()
    app.dependency_overrides[get_zoho_client] = lambda: client
    resp = TestClient(app).get("/health/zoho", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 503 and "OAUTH_SCOPE_MISMATCH" in resp.json()["detail"]


def test_scopes_command(capsys):
    assert zoho_auth.main(["scopes"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ZohoCRM.modules.accounts.READ,") and " " not in out
