from app.db.models import DocumentSource, DocumentStatus, JobStatus, StoredFile, VerificationJob
from app.pipeline.collect import collect_job
from app.storage.base import LocalStorage
from app.zoho.auth import ZohoTokenProvider
from app.zoho.client import ZohoClient
from tests.fakes import ACCOUNT_ID, EXE, JPEG, PDF, PNG, FakeZoho


def run(db, settings, fake):
    http = fake.http()
    client = ZohoClient(settings, ZohoTokenProvider(settings, http=http), http=http,
                        sleep=lambda _: None)
    job = VerificationJob(account_id=ACCOUNT_ID, reason="for activation po Ma'am")
    db.add(job)
    db.commit()
    storage = LocalStorage(settings.storage_local_dir)
    return collect_job(db, job, client, storage, settings), storage


def test_collects_all_three_sources(db, settings):
    fake = FakeZoho()
    fake.attachments = [
        {"id": "187", "name": "IMG_20260113_092731.jpg", "data": JPEG + b"a"},
        {"id": "185", "name": "IMG_20260113_092805.jpg", "data": JPEG + b"b"},
    ]
    fake.account["Business_Permit"] = [{"File_Id__s": "enc1", "File_Name__s": "permit.pdf"}]
    fake.files["enc1"] = PDF
    fake.notes = [{"id": "n1", "$attachments": [{"id": "na1", "File_Name": "bir.png"}]}]
    fake.files["na1"] = PNG

    job, storage = run(db, settings, fake)

    assert job.status == JobStatus.COLLECTED
    assert job.documents_found == 4
    assert job.customer_number == "EXAMPLE-001"
    sources = sorted(d.source for d in job.documents)
    assert sources == sorted([DocumentSource.ATTACHMENT, DocumentSource.ATTACHMENT,
                              DocumentSource.FILE_FIELD, DocumentSource.NOTE_ATTACHMENT])
    permit = next(d for d in job.documents if d.source == DocumentSource.FILE_FIELD)
    assert permit.source_field == "Business_Permit"
    assert permit.mime_type == "application/pdf"
    for f in db.query(StoredFile):
        assert storage.exists(f.storage_key)


def test_same_file_uploaded_twice_is_marked_duplicate(db, settings):
    fake = FakeZoho()
    fake.attachments = [
        {"id": "1", "name": "a.jpg", "data": JPEG},
        {"id": "2", "name": "a (1).jpg", "data": JPEG},
    ]
    job, _ = run(db, settings, fake)
    statuses = sorted(d.status for d in job.documents)
    assert statuses == [DocumentStatus.DUPLICATE, DocumentStatus.STORED]
    assert job.documents_found == 1
    assert db.query(StoredFile).count() == 1


def test_unsupported_file_is_rejected(db, settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "1", "name": "setup.exe", "data": EXE},
                        {"id": "2", "name": "permit.jpg", "data": JPEG}]
    job, _ = run(db, settings, fake)
    rejected = [d for d in job.documents if d.status == DocumentStatus.REJECTED]
    assert len(rejected) == 1 and "Unsupported" in rejected[0].status_detail
    assert job.status == JobStatus.COLLECTED


def test_no_documents(db, settings):
    job, _ = run(db, settings, FakeZoho())
    assert job.status == JobStatus.NO_DOCUMENTS
    assert job.completed_at is not None


def test_rerun_reuses_stored_file(db, settings):
    fake = FakeZoho()
    fake.attachments = [{"id": "1", "name": "a.jpg", "data": JPEG}]
    first, _ = run(db, settings, fake)
    first.status = JobStatus.READ  # finished; the account may be verified again
    db.commit()
    run(db, settings, fake)
    assert db.query(StoredFile).count() == 1
