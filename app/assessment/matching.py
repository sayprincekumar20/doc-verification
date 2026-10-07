"""Comparisons between documents and Zoho, tuned on real Philippine documents."""

import re

from app.extraction.normalize import alnum, business_key, normalize_name, numbers_conflict

SAME, LIKELY_SAME, DIFFERENT = "SAME", "LIKELY_SAME", "DIFFERENT"

# Address abbreviations and filler words (BIR/permit/Zoho spell the same place differently).
_ADDRESS_ABBREVIATIONS = {
    "BLK": "BLOCK", "BL": "BLOCK", "LT": "LOT", "BRGY": "BARANGAY", "BGY": "BARANGAY",
    "BRG": "BARANGAY", "ST": "STREET", "AVE": "AVENUE", "AV": "AVENUE", "RD": "ROAD",
    "PH": "PHASE", "SUBD": "SUBDIVISION", "VILL": "VILLAGE", "BLDG": "BUILDING",
    "FLR": "FLOOR", "EXT": "EXTENSION", "STO": "SANTO", "STA": "SANTA", "GEN": "GENERAL",
    "PROV": "PROVINCE", "MUN": "MUNICIPALITY",
}
_ADDRESS_FILLER = {"BARANGAY", "CITY", "OF", "PROVINCE", "MUNICIPALITY", "PHILIPPINES", "THE",
                   "DISTRICT", "NCR", "REGION", "AND"}
_NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV"}


def _tokens(text: str) -> list[str]:
    text = str(text).upper().replace("Ñ", "N")
    # split digits from letters: "LOT20" -> "LOT 20", "B11" -> "B 11", "PH3" -> "PH 3"
    text = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", text)
    return re.findall(r"[A-Z0-9]+", text)


def address_tokens(text: str) -> list[str]:
    out = []
    for tok in _tokens(text):
        tok = _ADDRESS_ABBREVIATIONS.get(tok, tok)
        if tok not in _ADDRESS_FILLER:
            out.append(tok)
    return out


def match_address(a: str | None, b: str | None) -> str | None:
    """SAME when one address contains the other (Zoho often stores only part of it);
    DIFFERENT when numbers disagree (BLOCK 21 vs BLOCK 23) or the overlap is small."""
    if not a or not b:
        return None
    ta, tb = address_tokens(a), address_tokens(b)
    if numbers_conflict(" ".join(ta), " ".join(tb)):
        return DIFFERENT
    sa, sb = set(ta), set(tb)
    small, large = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if small and small <= large:
        return SAME
    overlap = len(sa & sb) / max(len(small), 1)
    return LIKELY_SAME if overlap >= 0.75 else DIFFERENT


def person_tokens(name: str) -> list[str]:
    return [t for t in _tokens(name.replace(",", " "))
            if len(t) > 1 and t not in _NAME_SUFFIXES]


def match_person(a: str | None, b: str | None) -> str | None:
    """'FLORES, MARICHELLE' == 'MARICHELLE FLORES'; 'FEDELINA M. DAUBA' == 'FEDELINA DAUBA';
    'JENNIFER WILSON' is LIKELY_SAME as 'WILSON, JENNIFER DELOS SANTOS' (middle name left out)."""
    if not a or not b:
        return None
    ta, tb = set(person_tokens(a)), set(person_tokens(b))
    if not ta or not tb:
        return None
    if ta == tb:
        return SAME
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(small) >= 2 and small <= large:
        return LIKELY_SAME
    return DIFFERENT


def match_business(a: str | None, b: str | None) -> str | None:
    """Business names: ignore punctuation, possessives and legal suffixes."""
    if not a or not b:
        return None
    ka, kb = business_key(a), business_key(b)
    if ka == kb or alnum(ka) == alnum(kb):
        return SAME
    if alnum(ka) in alnum(kb) or alnum(kb) in alnum(ka):
        return LIKELY_SAME  # "WALDS BISTRO" vs "WALDS BISTRO AND STEAKHOUSE"
    return DIFFERENT


def match_tin(a: str | None, b: str | None, *, ignore_branch: bool = False) -> str | None:
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    if len(da) < 9 or len(db) < 9:
        return None
    if da[:9] != db[:9]:
        return DIFFERENT
    if ignore_branch:
        return SAME
    return SAME if da[9:].lstrip("0") == db[9:].lstrip("0") else DIFFERENT


def natural_person_name(name: str) -> str:
    """'FLORES, MARICHELLE' -> 'MARICHELLE FLORES' (BIR prints LAST, FIRST MIDDLE)."""
    if "," in name:
        last, _, first = name.partition(",")
        return normalize_name(f"{first.strip()} {last.strip()}")
    return normalize_name(name)
