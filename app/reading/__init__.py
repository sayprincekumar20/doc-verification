"""Phase 1B: turn any stored file into clean page images + text.

    pages = read_document(data, mime_type)   # list[PageResult]

Pipeline per page: normalize format -> orient -> crop/perspective -> deskew -> enhance
-> quality score -> text (PDF text layer when present, otherwise Tesseract OCR).
"""

from app.reading.pipeline import PageResult, read_document  # noqa: F401
