"""Convert any supported file into page images (and a text layer when the PDF has one)."""

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps

try:  # HEIC/HEIF photos from iPhones
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

PDF_RENDER_DPI = 300
MIN_TEXT_LAYER_CHARS = 80   # fewer than this = scanned page, OCR it
MAX_PAGES = 30              # safety limit per file

OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
}


class UnreadableFileError(Exception):
    """Corrupt, password-protected or otherwise unopenable file."""


@dataclass
class RawPage:
    page_number: int            # 1-based
    image: np.ndarray           # RGB uint8
    text_layer: str | None      # PDF text when the page is digital, else None
    source: str                 # "pdf_text" | "pdf_scan" | "image" | "office"


def _pil_to_rgb(img: Image.Image) -> np.ndarray:
    img = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    return np.asarray(img.convert("RGB"))


def _from_image(data: bytes) -> list[RawPage]:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise UnreadableFileError(f"Cannot open image: {exc}") from exc
    pages = []
    for i in range(min(getattr(img, "n_frames", 1), MAX_PAGES)):  # multi-page TIFF
        img.seek(i)
        pages.append(RawPage(i + 1, _pil_to_rgb(img.copy()), None, "image"))
    return pages


def _from_pdf(data: bytes, source_override: str | None = None) -> list[RawPage]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnreadableFileError(f"Cannot open PDF: {exc}") from exc
    if doc.needs_pass:
        raise UnreadableFileError("PDF is password-protected")
    pages = []
    for page in list(doc)[:MAX_PAGES]:
        text = page.get_text().strip()
        pix = page.get_pixmap(dpi=PDF_RENDER_DPI, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        digital = len("".join(text.split())) >= MIN_TEXT_LAYER_CHARS
        source = source_override or ("pdf_text" if digital else "pdf_scan")
        pages.append(RawPage(page.number + 1, img.copy(), text if digital else None, source))
    return pages


def _from_office(data: bytes, mime_type: str) -> list[RawPage]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise UnreadableFileError("LibreOffice is not installed; cannot convert office files")
    ext = {"application/msword": "doc", "application/vnd.ms-excel": "xls"}.get(
        mime_type, "xlsx" if "spreadsheet" in mime_type else "docx")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input.{ext}"
        src.write_bytes(data)
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            capture_output=True, timeout=120,
        )
        pdf = Path(tmp) / "input.pdf"
        if result.returncode != 0 or not pdf.exists():
            raise UnreadableFileError("Office file could not be converted to PDF")
        return _from_pdf(pdf.read_bytes(), source_override="office")


def to_pages(data: bytes, mime_type: str) -> list[RawPage]:
    if mime_type == "application/pdf":
        pages = _from_pdf(data)
    elif mime_type in OFFICE_MIMES:
        pages = _from_office(data, mime_type)
    elif mime_type.startswith("image/"):
        pages = _from_image(data)
    else:
        raise UnreadableFileError(f"Unsupported type {mime_type}")
    if not pages:
        raise UnreadableFileError("File has no pages")
    return pages
