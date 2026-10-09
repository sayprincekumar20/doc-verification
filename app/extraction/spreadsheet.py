"""Read structured spreadsheets directly from their cells (no OCR, no AI).

Main use: RGF's own "CUSTOMER INFORMATION SHEET" (.xlsx), filled in by the customer. Its labels
sit in one column and the answers in the next (B->C, E->F). Every label/value pair is kept
(nothing is dropped); the important ones get a canonical key used for cross-checks and for
proposals to Zoho. It is customer-declared data: useful, but not an official document, so it
never outranks the BIR 2303 / DTI / permit.
"""

import io
import re
from datetime import date, datetime

import openpyxl

DOCUMENT_TYPE = "CUSTOMER_INFO_SHEET"
CONFIDENCE = 0.9   # read exactly as typed, but self-declared by the customer

# normalized label -> canonical key (labels on the RGF form, 2026 version)
LABELS = {
    "date submitted": "date_submitted",
    "company name (legal entity)": "company_name",
    "business style / trade name": "business_style",
    "display name (on sales document)": "display_name",
    "customer type": "customer_type",
    "tin (incl. branch code)": "tin",
    "vat type": "vat_type",
    "sec / dti reg. no.": "registration_no",
    "industry / business nature": "industry",
    "years in operation": "years_in_operation",
    "billing address": "billing_address",
    "delivery address (if different)": "delivery_address",
    "city / province": "city_province",
    "zip code": "zip_code",
    "requested credit term": "requested_credit_term",
    "payment method": "payment_method",
    "receiving person / department": "receiving_person",
    "primary contact person": "contact_person",
    "position / department": "contact_position",
    "email address": "email",
    "mobile / landline (multiple)": "phone",
    "backup contact person": "backup_contact_person",
    "backup email / number": "backup_contact",
    "receiver 1 — full name": "receiver_1",
    "receiver 2 — full name": "receiver_2",
    "receiver 3 — full name": "receiver_3",
    "rg account manager/owner — name": "rg_account_manager",
}
KINDS = {"tin": "tin", "date_submitted": "date", "zip_code": "code", "registration_no": "code",
         "email": "email", "phone": "code", "company_name": "name", "business_style": "text",
         "display_name": "name", "contact_person": "name", "billing_address": "address",
         "delivery_address": "address"}


def _norm_label(label: str) -> str:
    s = label.replace("*", "").replace(":", "").strip().lower()
    return re.sub(r"\s+", " ", s)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm_label(label)).strip("_")[:60]


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).replace("_x0009_", " ").replace("\r", " ").replace("\t", " ")
    s = " ".join(s.split())
    return s or None


def phone_ph(value: str) -> str:
    """Excel drops the leading 0 of PH mobiles: 9177136809 -> 09177136809."""
    digits = re.sub(r"\D", "", value)
    return "0" + digits if len(digits) == 10 and digits.startswith("9") else value


def label_value_pairs(data: bytes) -> list[dict]:
    """Every (label, value) pair: a text cell followed by a filled cell to its right."""
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    pairs = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            cells = [c for c in row if c.value not in (None, "")]
            columns = [c.column for c in cells]
            if any(columns[i:i + 5] == list(range(columns[i], columns[i] + 5))
                   for i in range(len(columns))):
                continue  # 5+ filled cells side by side: a table header/row, not label: value
            for i, cell in enumerate(cells[:-1]):
                nxt = cells[i + 1]
                if not isinstance(cell.value, str) or nxt.column != cell.column + 1:
                    continue
                label, value = _clean(cell.value), _clean(nxt.value)
                if label and value and len(label) <= 80:
                    pairs.append({"sheet": ws.title, "cell": nxt.coordinate,
                                  "label": label, "value": value})
    return pairs


def is_customer_info_sheet(data: bytes) -> bool:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001 - not a readable workbook
        return False
    for ws in wb.worksheets:
        for row in ws.iter_rows(max_row=6):
            for c in row:
                if isinstance(c.value, str) and "CUSTOMER INFORMATION SHEET" in c.value.upper():
                    return True
    return False


def extract_customer_info_sheet(data: bytes) -> dict:
    """{document_type, fields{key: field result}, other_fields[...]} in the same shape as AI
    extraction results, so the assessment treats it like any other document."""
    from app.extraction.normalize import normalize

    fields, other = {}, []
    for p in label_value_pairs(data):
        key = LABELS.get(_norm_label(p["label"]))
        if key is None:
            other.append({"label": p["label"], "value": p["value"], "page": None,
                          "location": f"{p['sheet']}!{p['cell']}"})
            continue
        value = phone_ph(p["value"]) if key in ("phone",) else p["value"]
        kind = KINDS.get(key, "text")
        fields[key] = {
            "name": key, "kind": kind, "value": value, "normalized": normalize(kind, value),
            "evidence": f"{p['label']} ({p['sheet']}!{p['cell']})", "page": None,
            "grounding": "EXACT", "ocr_match": None, "confidence": CONFIDENCE,
            "zoho_field": None, "issues": [],
        }
    return {"document_type": DOCUMENT_TYPE, "fields": fields, "other_fields": other,
            "validity_status": "NO_EXPIRY", "valid_until": None, "issues": []}
