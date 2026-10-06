import time
from dataclasses import dataclass

import numpy as np

from app.reading.normalize import RawPage, to_pages
from app.reading.ocr import DEFAULT_LANG, OcrResult, run_ocr
from app.reading.preprocess import Prepared, ink_filter, prepare
from app.reading.quality import Quality, assess


@dataclass
class PageResult:
    page_number: int
    source: str              # pdf_text | pdf_scan | image | office
    text: str                # best single reading of the page
    grounding_text: str      # all readings combined; used to check AI-extracted values
    text_method: str         # "text_layer" | "ocr" | "ocr+ink"
    ocr: OcrResult | None
    quality: Quality
    prepared: Prepared       # cleaned images (color for the vision model, gray for OCR)
    seconds: float


SECOND_PASS_BELOW_CONF = 80.0
PHOTO_MIN_LONG_SIDE = 2000


def _read_page(raw: RawPage, lang: str) -> PageResult:
    started = time.monotonic()
    # Edge-crop only camera photos. On scans/screenshots it cut table cells (permit: 6/13 -> 3/13).
    is_photo = raw.source == "image" and max(raw.image.shape[:2]) >= PHOTO_MIN_LONG_SIDE
    prepared = prepare(raw.image, is_photo=is_photo)
    if raw.text_layer:
        ocr, text, method = None, raw.text_layer, "text_layer"
        quality = assess(raw.image, prepared.ocr, 100.0, len(text.split()), text_layer=True)
        grounding = text
    else:
        ocr = run_ocr(prepared.ocr, lang=lang)
        text, method, grounding = ocr.text, "ocr", ocr.text
        if ocr.mean_conf < SECOND_PASS_BELOW_CONF:
            # Low confidence: often a security background. Read the dark ink only as well.
            ink = run_ocr(ink_filter(prepared.ocr), lang=lang)
            grounding, method = f"{ocr.text}\n{ink.text}", "ocr+ink"
        quality = assess(raw.image, prepared.ocr, ocr.mean_conf, len(ocr.words))
    return PageResult(raw.page_number, raw.source, text, grounding, method, ocr, quality,
                      prepared, round(time.monotonic() - started, 2))


def read_document(data: bytes, mime_type: str, lang: str = DEFAULT_LANG) -> list[PageResult]:
    return [_read_page(raw, lang) for raw in to_pages(data, mime_type)]


def to_png(image: np.ndarray) -> bytes:
    import cv2

    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if image.ndim == 3 else image
    return cv2.imencode(".png", bgr)[1].tobytes()
