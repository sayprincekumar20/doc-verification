"""Phase 1B job step: read stored files, save pages, reuse readings for identical content."""

import hashlib
import shutil

import pytest

from app.db.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    FilePage,
    FileReading,
    JobStatus,
    StoredFile,
    VerificationJob,
)
from app.pipeline import read as read_module
from app.pipeline.read import read_job
from app.storage.base import LocalStorage, storage_key_for
from tests import synthetic_docs as sd

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None,
                                reason="Tesseract not installed")


def _job_with_file(db, storage, data: bytes, mime: str, ext: str, name: str):
    sha = hashlib.sha256(data).hexdigest()
    if db.get(StoredFile, sha) is None:
        key = storage_key_for(sha, ext)
        storage.put(key, data, mime)
        db.add(StoredFile(sha256=sha, size_bytes=len(data), mime_type=mime, extension=ext,
                          storage_key=key))
    job = VerificationJob(account_id="1000000000000001", status=JobStatus.COLLECTED)
    db.add(job)
    db.flush()
    db.add(Document(job_id=job.id, source=DocumentSource.ATTACHMENT, zoho_file_ref="1",
                    file_name=name, sha256=sha, status=DocumentStatus.STORED))
    db.commit()
    return job, sha


def test_read_job_saves_pages_and_classifies(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    job, sha = _job_with_file(db, storage, sd.sideways_png_no_exif(), "image/png", "png",
                              "IMG_0001.png")
    read_job(db, job, storage)

    assert job.status == JobStatus.READ
    reading = db.get(FileReading, (sha, read_module.PIPELINE_VERSION))
    assert reading.status == "READ" and reading.document_type == "DTI_BN_CERT"
    page = db.query(FilePage).one()
    assert page.rotation in (90, 270) and "1234567" in page.grounding_text
    assert storage.exists(page.image_key)  # cleaned page image for reviewers / vision model


def test_same_content_is_never_read_twice(db, settings, monkeypatch):
    storage = LocalStorage(settings.storage_local_dir)
    data = sd.to_bytes(sd.page_image())
    first, _ = _job_with_file(db, storage, data, "image/png", "png", "a.png")
    read_job(db, first, storage)

    def boom(*_a, **_k):
        raise AssertionError("OCR should not run again for identical content")

    monkeypatch.setattr(read_module, "read_document", boom)
    second, _ = _job_with_file(db, storage, data, "image/png", "png", "copy.png")
    read_job(db, second, storage)
    assert second.status == JobStatus.READ
    assert db.query(FilePage).count() == 1


def test_corrupt_file_is_marked_unreadable_and_job_continues(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    job, sha = _job_with_file(db, storage, b"\x89PNG\r\n\x1a\n broken", "image/png", "png",
                              "broken.png")
    read_job(db, job, storage)
    reading = db.get(FileReading, (sha, read_module.PIPELINE_VERSION))
    assert reading.status == "UNREADABLE" and reading.error
    assert job.status == JobStatus.READ


def test_digital_pdf_page_uses_text_layer(db, settings):
    storage = LocalStorage(settings.storage_local_dir)
    job, sha = _job_with_file(db, storage, sd.digital_pdf(), "application/pdf", "pdf", "x.pdf")
    read_job(db, job, storage)
    page = db.query(FilePage).one()
    assert page.text_method == "text_layer" and page.document_type == "DTI_BN_CERT"
