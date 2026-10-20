"""The Verify Account button sends the Account record, Attachments and Notes it already read in
Zoho; the engine uses them instead of calling Zoho again (only files are downloaded)."""

from app.db.models import JobStatus, VerificationJob
from app.pipeline.collect import collect_job
from app.storage.base import LocalStorage
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from tests.fakes import ACCOUNT_ID, JPEG, PDF, PNG, FakeZoho
from tests.test_api_jobs import HEADERS, api, payload  # noqa: F401 - pytest fixture

ACCOUNT = {"id": ACCOUNT_ID, "Account_Name": "Example Market",
           "Inventory_Customer_Number": "EXAMPLE-001", "Customer_Status": "Inactive",
           "Business_Permit": [{"File_Id__s": "enc1", "File_Name__s": "permit.pdf"}]}
ATTACHMENTS = [{"id": "187", "File_Name": "dti.jpg", "Created_Time": "2026-10-20T09:00:00+08:00",
                "Created_By": {"name": "Rep", "id": "5"}}]
NOTES = [{"id": "n1", "$attachments": [{"id": "na1", "File_Name": "bir.png"}]}]


def test_api_accepts_button_data(api, db):  # noqa: F811
    resp = api.post("/v1/jobs", headers=HEADERS,
                    json=payload(account=ACCOUNT, attachments=ATTACHMENTS, notes=NOTES))
    assert resp.status_code == 202
    job = db.query(VerificationJob).one()
    assert job.button_data["account"]["Account_Name"] == "Example Market"
    assert job.button_data["attachments"][0]["id"] == "187"


def test_api_rejects_account_of_another_record(api):  # noqa: F811
    other = {**ACCOUNT, "id": "9999999999999999"}
    assert api.post("/v1/jobs", headers=HEADERS,
                    json=payload(account=other)).status_code == 422


def _collect(db, settings, fake, button_data):
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    job = VerificationJob(account_id=ACCOUNT_ID, button_data=button_data)
    db.add(job)
    db.commit()
    return collect_job(db, job, client, LocalStorage(settings.storage_local_dir), settings)


def _lists_called(fake):
    base = f"/crm/v8/Accounts/{ACCOUNT_ID}"
    return {c for c in fake.calls if c in (f"GET {base}", f"GET {base}/Attachments",
                                           f"GET {base}/Notes")}


def test_collection_uses_button_data_and_only_downloads_files(db, settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "187", "name": "dti.jpg", "data": JPEG}]
    fake.files["enc1"], fake.files["na1"] = PDF, PNG
    job = _collect(db, settings, fake, {"account": ACCOUNT, "attachments": ATTACHMENTS,
                                        "notes": NOTES})
    assert job.status == JobStatus.COLLECTED and job.documents_found == 3
    assert _lists_called(fake) == set()          # no Account / Attachments / Notes reads
    assert job.account_snapshot["Customer_Status"] == "Inactive"
    assert job.button_data is None               # cleared after use


def test_notes_without_attachment_info_are_read_by_the_engine(db, settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "187", "name": "dti.jpg", "data": JPEG}]
    fake.files["enc1"] = PDF
    _collect(db, settings, fake, {"account": ACCOUNT, "attachments": ATTACHMENTS,
                                  "notes": [{"id": "n1", "Note_Title": "x"}]})
    assert _lists_called(fake) == {f"GET /crm/v8/Accounts/{ACCOUNT_ID}/Notes"}


def test_without_button_data_engine_reads_zoho_as_before(db, settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "187", "name": "dti.jpg", "data": JPEG}]
    _collect(db, settings, fake, None)
    assert len(_lists_called(fake)) == 3
