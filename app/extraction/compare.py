"""Are two values for the same field the same? Used to score extraction against the gold set,
and later (Phase 1D) to compare documents with each other and with Zoho."""

from difflib import SequenceMatcher

from app.extraction.normalize import (
    alnum,
    business_key,
    name_key,
    normalize,
    numbers_conflict,
    strip_code_label,
)

SAME, MINOR_DIFF, DIFFERENT = "SAME", "MINOR_DIFF", "DIFFERENT"


def compare(kind: str, a: str | None, b: str | None) -> str | None:
    """None when either side is empty. MINOR_DIFF = same meaning, small text difference
    (e.g. an address missing a word); never used for TINs, codes, dates or money."""
    if not a or not b:
        return None
    if kind in ("date", "tin", "money"):
        na, nb = normalize(kind, a), normalize(kind, b)
        return SAME if na and na == nb else DIFFERENT
    if kind in ("code", "number"):
        return SAME if alnum(strip_code_label(a)) == alnum(strip_code_label(b)) else DIFFERENT
    if kind == "choice":
        return SAME if normalize("choice", a) == normalize("choice", b) else DIFFERENT
    if kind == "name":
        if name_key(a) == name_key(b) or business_key(a) == business_key(b):
            return SAME
    if alnum(a) == alnum(b):
        return SAME
    if numbers_conflict(a, b):
        return DIFFERENT  # "BLOCK 21" vs "BLOCK 23" is a different address, not a typo
    ratio = SequenceMatcher(None, alnum(a), alnum(b)).ratio()
    return MINOR_DIFF if ratio >= 0.9 else DIFFERENT
