"""Cross-check an AI-extracted value against the OCR text of the same page(s).

EXACT     the value (or a known rendering of it) appears in the OCR text
FUZZY     names/addresses/text only: a close match appears (OCR misread a character or two)
CONFLICT  TINs, numbers, codes, dates, money: OCR read something close but DIFFERENT
          (e.g. 2901359 vs 2901350). One digit decides, so a person must check the image.
NOT_FOUND nothing similar: the reviewer must look at the image for this field
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.extraction.normalize import alnum, date_renderings, parse_date

EXACT, FUZZY, CONFLICT, NOT_FOUND = "EXACT", "FUZZY", "CONFLICT", "NOT_FOUND"
EXACT_KINDS = {"tin", "code", "number", "money", "date"}
FUZZY_MIN_RATIO = 0.85


@dataclass
class Grounding:
    status: str
    score: float      # 1.0 exact, ratio for fuzzy, 0 not found
    matched: str | None = None


def _best_window(needle: str, haystack: str) -> tuple[float, str]:
    n = len(needle)
    if n == 0 or len(haystack) < n:
        return 0.0, ""
    best, best_text = 0.0, ""
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)
    for size in (n - 1, n, n + 1):
        if size <= 0:
            continue
        for i in range(0, len(haystack) - size + 1):
            window = haystack[i:i + size]
            matcher.set_seq1(window)
            if matcher.real_quick_ratio() < best or matcher.quick_ratio() < best:
                continue
            ratio = matcher.ratio()
            if ratio > best:
                best, best_text = ratio, window
    return best, best_text


def ground(value: str | None, kind: str, ocr_text: str) -> Grounding:
    if value is None or str(value).strip() == "":
        return Grounding(NOT_FOUND, 0.0)
    text = alnum(ocr_text)
    candidates = [str(value)]
    if kind == "date" and (d := parse_date(value)):
        candidates += date_renderings(d)
    for cand in candidates:
        if (needle := alnum(cand)) and needle in text:
            return Grounding(EXACT, 1.0, cand)
    needle = alnum(value)
    if len(needle) < 4:  # too short to fuzzy-match safely ("BR", "56")
        return Grounding(NOT_FOUND, 0.0)
    ratio, window = _best_window(needle, text)
    if ratio >= FUZZY_MIN_RATIO:
        status = CONFLICT if kind in EXACT_KINDS else FUZZY
        return Grounding(status, round(ratio, 3), window)
    return Grounding(NOT_FOUND, 0.0)
