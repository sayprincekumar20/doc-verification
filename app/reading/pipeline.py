import time
from dataclasses import dataclass

import numpy as np

from app.reading.normalize import RawPage, to_pages
from app.reading.ocr import DEFAULT_LANG, OcrResult, run_ocr
from app.reading.preprocess import Prepared, adaptive_binarize, ink_filter, prepare
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
SECOND_PASS_BELOW_WORDS = 40   # a business document page has far more words than this
PHOTO_MIN_LONG_SIDE = 2000


def _readable_words(result: OcrResult) -> int:
    return sum(1 for w in result.words
               if w.conf >= 70 and len(w.text) >= 3 and w.text.isalpha())


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
        low_conf = ocr.mean_conf < SECOND_PASS_BELOW_CONF
        few_words = len(ocr.words) < SECOND_PASS_BELOW_WORDS
        if low_conf or few_words:
            # Weak first reading. Low confidence usually means a security background (BIR
            # watermark): read the dark ink only. Very few words usually means dark areas broke
            # Tesseract's global threshold (Makati permit: 1 word): threshold locally. The
            # adaptive pass is NOT run on watermarked pages: it turns the pattern into noise
            # and made one scanned 2303 take 230 s instead of 25 s.
            readings = {"ocr": ocr, "ink": run_ocr(ink_filter(prepared.ocr), lang=lang)}
            if few_words:
                readings["adaptive"] = run_ocr(adaptive_binarize(prepared.ocr), lang=lang)
            best = max(readings, key=lambda k: _readable_words(readings[k]))
            ocr = readings[best]
            text = ocr.text
            grounding = "\n".join(r.text for r in readings.values())
            method = "ocr+ink" if best == "ocr" else f"ocr+{best}"
        quality = assess(raw.image, prepared.ocr, ocr.mean_conf, len(ocr.words))
    return PageResult(raw.page_number, raw.source, text, grounding, method, ocr, quality,
                      prepared, round(time.monotonic() - started, 2))


def read_document(data: bytes, mime_type: str, lang: str = DEFAULT_LANG) -> list[PageResult]:
    return [_read_page(raw, lang) for raw in to_pages(data, mime_type)]


def to_png(image: np.ndarray) -> bytes:
    import cv2

    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if image.ndim == 3 else image
    return cv2.imencode(".png", bgr)[1].tobytes()
