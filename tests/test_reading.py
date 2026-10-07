"""Phase 1B: format normalization, orientation, OCR, quality and classification."""

import shutil

import numpy as np
import pytest

from app.reading import read_document
from app.reading.classify import classify_text
from app.reading.normalize import UnreadableFileError, to_pages
from tests import synthetic_docs as sd

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None,
                                     reason="Tesseract not installed")


# ---------- normalization (no OCR) ----------

def test_exif_rotation_is_applied():
    page = to_pages(sd.sideways_jpeg_with_exif(), "image/jpeg")[0]
    h, w = page.image.shape[:2]
    assert h > w  # back to portrait


def test_transparent_png_becomes_rgb_on_white():
    from PIL import Image
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    page = to_pages(sd.to_bytes(img), "image/png")[0]
    assert page.image.shape == (300, 400, 3) and int(page.image.mean()) == 255


def test_digital_pdf_uses_text_layer():
    page = to_pages(sd.digital_pdf(), "application/pdf")[0]
    assert page.source == "pdf_text" and "Business Name No. 1234567" in page.text_layer


def test_scanned_pdf_has_no_text_layer():
    page = to_pages(sd.scanned_pdf(), "application/pdf")[0]
    assert page.source == "pdf_scan" and page.text_layer is None
    assert page.image.shape[0] > 3000  # rendered at 300 DPI


def test_password_pdf_is_unreadable():
    with pytest.raises(UnreadableFileError, match="password"):
        to_pages(sd.encrypted_pdf(), "application/pdf")


def test_corrupt_image_is_unreadable():
    with pytest.raises(UnreadableFileError):
        to_pages(b"\xff\xd8\xff\xe0 not really a jpeg", "image/jpeg")


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_office_document_is_converted():
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        txt = Path(tmp) / "doc.txt"
        txt.write_text("\n".join(sd.DTI_LINES), encoding="utf-8")
        subprocess.run(["soffice", "--headless", "--convert-to", "docx", "--outdir", tmp,
                        str(txt)], capture_output=True, timeout=120, check=True)
        data = (Path(tmp) / "doc.docx").read_bytes()
    page = to_pages(data, "application/vnd.openxmlformats-officedocument."
                          "wordprocessingml.document")[0]
    assert page.source == "office" and "1234567" in (page.text_layer or "")


# ---------- full reading (OCR) ----------

@needs_tesseract
def test_sideways_photo_without_exif_is_turned_upright_and_read():
    page = read_document(sd.sideways_png_no_exif(), "image/png")[0]
    assert page.prepared.rotation in (90, 270)
    assert "1234567" in page.text and "JUAN DELA CRUZ" in page.text
    assert page.quality.label in ("GOOD", "FAIR")


@needs_tesseract
def test_digital_pdf_skips_ocr():
    page = read_document(sd.digital_pdf(), "application/pdf")[0]
    assert page.text_method == "text_layer" and page.ocr is None
    assert page.quality.label == "GOOD"


@needs_tesseract
def test_blank_page_is_unreadable():
    from PIL import Image
    blank = sd.to_bytes(Image.new("RGB", (1200, 1600), "white"))
    page = read_document(blank, "image/png")[0]
    assert page.quality.label == "UNREADABLE"


@needs_tesseract
def test_low_confidence_page_gets_second_ink_pass(monkeypatch):
    from app.reading import pipeline
    monkeypatch.setattr(pipeline, "SECOND_PASS_BELOW_CONF", 101.0)  # force the second pass
    page = read_document(sd.to_bytes(sd.page_image()), "image/png")[0]
    assert page.text_method == "ocr+ink" and len(page.grounding_text) > len(page.text)


# ---------- classification ----------

@pytest.mark.parametrize("text,expected", [
    ("\n".join(sd.DTI_LINES), "DTI_BN_CERT"),
    ("BIR FORM 2303 CERTIFICATE OF REGISTRATION TIN & BRANCH CODE NAME OF TAXPAYER", "BIR_2303"),
    ("BUSINESS LICENSE and MAYOR'S PERMIT 2025 Business Permit No. 0420 Proprietor/Owner",
     "MAYORS_PERMIT"),
    ("Republic of the Philippines MAYOR'S BUSINESS PERMIT No. 2025 02655 CITY MAYOR",
     "MAYORS_PERMIT"),
    ("SECURITIES AND EXCHANGE COMMISSION CERTIFICATE OF INCORPORATION", "SEC_CERT"),
    ("BARANGAY BUSINESS CLEARANCE Punong Barangay", "BARANGAY_CLEARANCE"),
    ("Picnic shoulder 2000kgs Pork Riblets 500kgs", "OTHER"),
])
def test_classify_text(text, expected):
    assert classify_text(text).document_type == expected


def test_ocr_merged_words_still_match():
    assert classify_text("CERTIFICATEOFREGISTRATION TIN&BRANCHCODE NAMEOFTAXPAYER"
                         ).document_type == "BIR_2303"


def test_file_name_is_only_a_hint():
    weak = classify_text("some unrelated text", "BIR 2303 - CLIENT.png")
    assert weak.document_type == "OTHER"  # a file name alone is not enough
    content = classify_text("\n".join(sd.DTI_LINES), "BIR 2303 - CLIENT.png")
    assert content.document_type == "DTI_BN_CERT"  # content beats a misleading name


def test_quality_handles_tiny_images():
    from app.reading.quality import assess
    q = assess(np.zeros((300, 200, 3), np.uint8), np.zeros((300, 200), np.uint8), 30.0, 3)
    assert q.label == "UNREADABLE" and any("low resolution" in r for r in q.reasons)


def test_tin_id_card_is_a_government_id():
    text = ("REPUBLIC OF THE PHILIPPINES DEPARTMENT OF FINANCE BUREAU OF INTERNAL REVENUE "
            "DELA CRUZ, JUAN TIN: 123-456-789-000 BIRTH DATE: 03/03/1984 "
            "ISSUE DATE: 08/19/2020 SIGNATURE")
    assert classify_text(text).document_type == "GOVERNMENT_ID"


def test_bir_2303_still_wins_over_id_signals():
    text = ("BIR FORM 2303 CERTIFICATE OF REGISTRATION TIN & BRANCH CODE NAME OF TAXPAYER "
            "SIGNATURE ISSUE DATE")
    assert classify_text(text).document_type == "BIR_2303"
