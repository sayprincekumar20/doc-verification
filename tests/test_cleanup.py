"""KEEP_DOCUMENT_FILES=false: no document copies kept after a job; results stay; re-checks
re-download and re-read but reuse the AI extraction (no new AI call)."""

import hashlib
import shutil
from datetime import date

import pytest

from app.db.models import (
    Document,
    DocumentExtraction,
    DocumentSource,
    DocumentStatus,
    FilePage,
    FileReading,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.pipeline.cleanup import purge_job_files
from app.pipeline.collect import _store_file
from app.pipeline.extract import extract_job
from app.pipeline.read import read_job
from app.storage.base import LocalStorage
from tests import synthetic_docs as sd
from tests.fake_vision import FakeVision

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="Tesseract not installed")
VALUES = {"business_name": "EXAMPLE MARKET", "owner_name": "JUAN DELA CRUZ",
          "bn_number": "1234567", "valid_from": "11 May 2021", "valid_to": "11 May 2026"}
DATA = sd.to_bytes(sd.page_image())
SHA = hashlib.sha256(DATA).hexdigest()


def _job(db, storage, status=JobStatus.COLLECTED, account="1000000000000001"):
    """Collect step stand-in: store the file (again if deleted) and link it to a new job."""
    _store_file(db, storage, DATA, SHA, "png", "image/png")
    job = VerificationJob(account_id=account, status=status)
    db.add(job)
    db.flush()
    db.add(Document(job_id=job.id, source=DocumentSource.ATTACHMENT, zoho_file_ref="1",
                    file_name="dti.png", sha256=SHA, status=DocumentStatus.STORED))
    db.commit()
    return job


def _run(db, storage, ai, job):
    read_job(db, job, storage)
    extract_job(db, job, storage, ai, today=date(2026, 10, 9))


def test_purge_deletes_copies_keeps_results(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    job = _job(db, storage)
    _run(db, storage, FakeVision(VALUES, "DTI_BN_CERT"), job)
    page = db.query(FilePage).one()
    image_key, file_key = page.image_key, db.get(StoredFile, SHA).storage_key
    assert storage.exists(image_key) and storage.exists(file_key)

    summary = purge_job_files(db, job, storage)
    assert summary == {"files_deleted": 1, "page_images_deleted": 1,
                       "kept_for_other_jobs": 0, "errors": []}
    assert not storage.exists(image_key) and not storage.exists(file_key)
    assert db.query(FilePage).count() == 0 and db.query(FileReading).count() == 0
    assert db.get(StoredFile, SHA) is not None                 # fingerprint kept
    assert db.query(DocumentExtraction).one().fields["bn_number"]["value"] == "1234567"


def test_recheck_rereads_but_reuses_ai_result(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    ai = FakeVision(VALUES, "DTI_BN_CERT")
    first = _job(db, storage)
    _run(db, storage, ai, first)
    first.status = JobStatus.EXTRACTED
    db.commit()
    purge_job_files(db, first, storage)

    second = _job(db, storage)                                 # file downloaded again
    assert storage.exists(db.get(StoredFile, SHA).storage_key)
    _run(db, storage, ai, second)
    assert second.status == JobStatus.EXTRACTED
    assert db.query(FilePage).count() == 1                     # OCR redone (free)
    assert len(ai.calls) == 1                                  # AI not called again


def test_file_used_by_another_running_job_is_kept(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    done = _job(db, storage)
    _run(db, storage, FakeVision(VALUES, "DTI_BN_CERT"), done)
    done.status = JobStatus.EXTRACTED
    db.commit()
    _job(db, storage, status=JobStatus.READING, account="1000000000000002")  # still running
    summary = purge_job_files(db, done, storage)
    assert summary["kept_for_other_jobs"] == 1 and summary["files_deleted"] == 0
    assert storage.exists(db.get(StoredFile, SHA).storage_key)
