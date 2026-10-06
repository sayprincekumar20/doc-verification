"""Normalize extracted values so they can be compared with OCR text, other documents and Zoho."""

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

_LEGAL_SUFFIXES = r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|OPC|LTD|LIMITED)\b"


def alnum(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def parse_date(value: str) -> date | None:
    """Philippine document dates: '11 May 2021', 'August 25, 2021', '1/22/2025' (month first),
    '2025-01-22', '22nd day of January 2025'."""
    if not value:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value).strip().lower())
    s = s.replace("day of", "").replace(",", " ").replace(".", " ")
    s = re.sub(r"\s+", " ", s).strip()
    try:
        if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s):
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            month, day = (b, a) if a > 12 else (a, b)  # PH convention: month first
            return date(y, month, day)
        if m := re.fullmatch(r"(\d{1,2}) ([a-z]+) (\d{4})", s):
            return date(int(m[3]), MONTHS[m[2]], int(m[1]))
        if m := re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4})", s):
            return date(int(m[3]), MONTHS[m[1]], int(m[2]))
    except (KeyError, ValueError):
        return None
    return None


def date_renderings(d: date) -> list[str]:
    """Ways a date may be printed, for checking it against OCR text."""
    full, short = d.strftime("%B"), d.strftime("%b")
    return [f"{d.day} {full} {d.year}", f"{d.day:02d} {full} {d.year}",
            f"{full} {d.day}, {d.year}", f"{full} {d.day:02d}, {d.year}",
            f"{d.day} {short} {d.year}", f"{short} {d.day}, {d.year}",
            f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}",
            d.isoformat()]


def normalize_tin(value: str) -> str | None:
    """Return 'NNN-NNN-NNN-NNNNN' (branch padded to 5) or None if it isn't a TIN."""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) not in (9, 12, 13, 14):
        return None
    branch = digits[9:].rjust(5, "0") if len(digits) > 9 else "00000"
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:9]}-{branch}"


def normalize_name(value: str) -> str:
    s = re.sub(r"[^A-Z0-9&' ]", " ", str(value).upper().replace("Ñ", "N"))
    return re.sub(r"\s+", " ", s).strip()


def name_key(value: str) -> str:
    """Order-insensitive key: 'FLORES, MARICHELLE' == 'MARICHELLE FLORES'."""
    return " ".join(sorted(normalize_name(value.replace(",", " ").replace("'", "")).split()))


def business_key(value: str) -> str:
    """Compare business names ignoring punctuation, possessives and legal suffixes:
    "WINNER'S MEAT SHOP" == "WINNER MEAT SHOP", "BISTRO AMERICANO CORP." == "BISTRO AMERICANO"."""
    s = normalize_name(value).replace("'S ", " ").replace("'", "")
    s = re.sub(_LEGAL_SUFFIXES, " ", s)
    s = re.sub(r"(\w)S\b", r"\1", s)  # WINNERS -> WINNER
    return re.sub(r"\s+", " ", s).strip()


def normalize_money(value: str) -> str | None:
    s = re.sub(r"[^\d.]", "", str(value))
    try:
        return f"{float(s):.2f}" if s else None
    except ValueError:
        return None


def normalize(kind: str, value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    if kind == "date":
        d = parse_date(value)
        return d.isoformat() if d else None
    if kind == "tin":
        return normalize_tin(value)
    if kind == "money":
        return normalize_money(value)
    if kind in ("code", "number"):
        return re.sub(r"\s+", " ", str(value).upper()).strip()
    return normalize_name(value) if kind in ("name", "address", "text") else str(value).strip()
