"""First-pass document classification from page text (Phase 1C step 1).

Keyword evidence per Philippine document type, tolerant of OCR noise. The vision model confirms
the type during extraction; disagreements go to the reviewer. File names are a weak hint only
(phone photos are named like IMG_20260113_092731.jpg).
"""

import re
from dataclasses import dataclass, field

TYPES = ("BIR_2303", "DTI_BN_CERT", "MAYORS_PERMIT", "SEC_CERT", "GIS", "BARANGAY_CLEARANCE",
         "GOVERNMENT_ID", "FOOD_SAFETY_PERMIT", "OTHER")

# (phrase, weight). Phrases are compared after uppercasing and removing non-letters/digits.
SIGNALS: dict[str, list[tuple[str, float]]] = {
    "BIR_2303": [("CERTIFICATE OF REGISTRATION", 2), ("BIR FORM", 2), ("2303", 1.5),
                 ("TIN BRANCH CODE", 3), ("NAME OF TAXPAYER", 3), ("RENTAS INTERNAS", 2),
                 ("REVENUE DISTRICT OFFICE", 2), ("TAXPAYER TYPE", 2), ("FILING FREQUENCY", 1)],
    "DTI_BN_CERT": [("BUSINESS NAME REGISTRATION", 4), ("BUSINESS NAME NO", 3),
                    ("ACT 3883", 3), ("REPUBLIC ACT NO 863", 2), ("DEPARTMENT OF TRADE", 2),
                    ("THIS CERTIFICATE ISSUED TO", 2), ("NOT A LICENSE TO ENGAGE", 2)],
    "MAYORS_PERMIT": [("MAYORS PERMIT", 4), ("MAYORS BUSINESS PERMIT", 4), ("BUSINESS PERMIT", 2),
                      ("BUSINESS LICENSE", 2), ("PERMIT NO", 1.5), ("LINE OF BUSINESS", 1),
                      ("NATURE OF BUSINESS", 1), ("MUNICIPAL TAX ORDINANCE", 2),
                      ("CITY MAYOR", 2), ("PROPRIETOR", 1), ("LOCAL GOVERNMENT CODE", 1)],
    "SEC_CERT": [("SECURITIES AND EXCHANGE COMMISSION", 4), ("CERTIFICATE OF INCORPORATION", 4),
                 ("ARTICLES OF INCORPORATION", 2), ("SEC REGISTRATION NO", 3)],
    "GIS": [("GENERAL INFORMATION SHEET", 5), ("STOCKHOLDERS", 1.5), ("DIRECTORS OFFICERS", 1.5)],
    "BARANGAY_CLEARANCE": [("BARANGAY CLEARANCE", 4), ("BARANGAY BUSINESS CLEARANCE", 5),
                           ("PUNONG BARANGAY", 2), ("TANGGAPAN NG PUNONG BARANGAY", 2)],
    "GOVERNMENT_ID": [("DRIVERS LICENSE", 4), ("PHILIPPINE IDENTIFICATION", 4), ("PHILSYS", 4),
                      ("PAMBANSANG PAGKAKAKILANLAN", 4), ("UNIFIED MULTIPURPOSE ID", 4),
                      ("SOCIAL SECURITY SYSTEM", 3), ("PROFESSIONAL REGULATION COMMISSION", 4),
                      ("PASSPORT", 3), ("POSTAL IDENTITY", 4), ("VOTERS ID", 4),
                      ("DATE OF BIRTH", 2), ("BIRTH DATE", 2), ("ISSUE DATE", 1),
                      ("SIGNATURE", 1), ("DEPARTMENT OF FINANCE", 1), ("LICENSE NO", 2)],
    "FOOD_SAFETY_PERMIT": [("NATIONAL MEAT INSPECTION", 4), ("FOOD AND DRUG ADMINISTRATION", 3),
                           ("LICENSE TO OPERATE", 3), ("SANITARY PERMIT", 3),
                           ("ACCREDITATION", 1)],
}
FILENAME_HINTS = {"BIR_2303": ("2303", "BIR", "COR"), "DTI_BN_CERT": ("DTI",),
                  "MAYORS_PERMIT": ("PERMIT", "MAYOR"), "SEC_CERT": ("SEC",), "GIS": ("GIS",),
                  "BARANGAY_CLEARANCE": ("BRGY", "BARANGAY"), "GOVERNMENT_ID": ("ID",)}
MIN_SCORE = 3.0


@dataclass
class Classification:
    document_type: str
    confidence: float                     # 0-1
    signals: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def _norm(text: str) -> str:
    letters = re.sub(r"[^A-Z0-9 ]", "", text.upper().replace("\n", " "))
    return " " + re.sub(r"\s+", " ", letters) + " "


def classify_text(text: str, file_name: str | None = None) -> Classification:
    norm = _norm(text)
    squashed = norm.replace(" ", "")
    scores: dict[str, float] = {}
    found: dict[str, list[str]] = {}
    for doc_type, signals in SIGNALS.items():
        for phrase, weight in signals:
            if phrase.replace(" ", "") in squashed:  # tolerate OCR-merged words
                scores[doc_type] = scores.get(doc_type, 0) + weight
                found.setdefault(doc_type, []).append(phrase)
    name = (file_name or "").upper()
    for doc_type, hints in FILENAME_HINTS.items():
        if any(re.search(rf"(^|[^A-Z]){h}([^A-Z]|$)", name) for h in hints):
            scores[doc_type] = scores.get(doc_type, 0) + 1.0
            found.setdefault(doc_type, []).append(f"file name: {name}")
    if not scores or max(scores.values()) < MIN_SCORE:
        return Classification("OTHER", 0.0, [], scores)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = round(min(1.0, top / 10) * (1 - second / (top + second)) * 2, 2)
    return Classification(best, min(confidence, 1.0), found[best], scores)
