"""Build the Zoho "Document Verifications" record (with its Proposed Changes rows) from an
assessment. API names default to the ones Zoho generated in the sandbox
(scripts/check_zoho_setup.py saves them to zoho/review_module_api_names.json, which overrides)."""

import json
import re
from pathlib import Path
from typing import Any

NAMES_FILE = Path("zoho/review_module_api_names.json")

DEFAULT_NAMES = {
    "module": "Document_Verifications",
    "fields": {
        "Account": "Account", "Review Status": "Review_Status",
        "Recommendation": "Recommendation", "Recommendation Reasons": "Recommendation_Reasons",
        "Alerts": "Alerts", "Required Documents": "Required_Documents",
        "Documents Found": "Documents_Found", "Lowest Confidence": "Lowest_Confidence",
        "Job ID": "Job_ID", "Rules Version": "Rules_Version", "Requested By": "Requested_By",
        "Request Reason": "Request_Reason", "Proposed Changes": "Proposed_Changes",
    },
    "subform_fields": {
        "Zoho Field": "Zoho_Field", "Field Label": "Field_Label",
        "Current Value": "Current_Value", "Proposed Value": "Proposed_Value",
        "Action": "Action", "Confidence": "Confidence", "OCR Check": "OCR_Check",
        "Evidence": "Evidence", "Auto Applied": "Auto_Applied", "Decision": "Decision",
    },
}

FIELD_LABELS = {
    "Owner_Name": "Owner Name", "Tax_Identification_Number_TIN": "TIN",
    "Type_of_Business_Organization": "Type of Business Organization",
    "Invoice_Company_Name": "Invoice Company Name", "Business_Style": "Business Style",
    "Billing_Street": "Billing Street", "Account_Name": "Account Name",
    "Business_Permit": "Business Permit (file)",
    "BIR_Registration_COR": "BIR Registration COR (file)",
    "Business_registration": "Business Registration (file)",
}
# Rows a reviewer must decide on (others are shown for context only).
NEEDS_DECISION = {"FILL", "CORRECT", "HOLD", "REVIEW_CONFLICT", "NO_PICKLIST_VALUE", "DIFFERS",
                  "ATTACH"}
ACTION_VALUES = {"FILL", "CORRECT", "MATCH", "HOLD", "REVIEW_CONFLICT", "DIFFERS", "ATTACH",
                 "NO_PICKLIST_VALUE"}
OCR_VALUES = {"EXACT", "FUZZY", "CROSS_CHECKED", "NOT_FOUND", "CONFLICT", "UNVERIFIABLE"}
AUTO_APPLIED = {"APPLIED": "Applied", "SHADOW": "Shadow", "SKIPPED_CHANGED": "Skipped",
                "FAILED": "Failed"}
MAX_ROWS = 25


def load_names(path: Path = NAMES_FILE) -> dict:
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        return {"module": saved.get("module") or DEFAULT_NAMES["module"],
                "fields": {**DEFAULT_NAMES["fields"], **(saved.get("fields") or {})},
                "subform_fields": {**DEFAULT_NAMES["subform_fields"],
                                   **(saved.get("subform_fields") or {})}}
    return DEFAULT_NAMES


_FETCH_PREFIX = re.compile(r"\b(?:attachment|file_field|note_attachment)_\d+_")


def short_name(name: str | None) -> str:
    """'attachment_5906238000061698185_IMG_20260113_092805.jpg' -> 'IMG_20260113_092805.jpg'."""
    return _FETCH_PREFIX.sub("", name or "")


def _text(value: Any, limit: int = 255, multiline: bool = False) -> str | None:
    """Zoho text value. Multi-line fields keep one item per line; single-line fields are
    collapsed. Long values are cut with an ellipsis."""
    if value in (None, "", []):
        return None
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = short_name(s)
    if multiline:
        s = "\n".join(" ".join(line.split()) for line in s.splitlines() if line.strip())
    else:
        s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _evidence(p: dict) -> str | None:
    """One line per source document: 'DTI_BN_CERT IMG_..731.jpg: MARICHELLE FLORES (EXACT
    0.95)', then the reason."""
    lines = []
    for src in p.get("sources") or []:
        line = " ".join(b for b in (src.get("type"), short_name(src.get("document"))) if b)
        if src.get("value"):
            line += f": {src['value']}"
        check = " ".join(str(x) for x in (src.get("grounding"), src.get("confidence"))
                         if x not in (None, ""))
        if check:
            line += f" ({check})"
        lines.append(line)
    if p.get("reason"):
        lines.append(p["reason"])
    return _text("\n".join(lines), 2000, multiline=True) if lines else None


def review_status(assessment: dict) -> str:
    actionable = [p for p in assessment["proposals"] if p["action"] in NEEDS_DECISION
                  and p.get("auto_apply_status") != "APPLIED"]
    if actionable:
        return "Pending Review"
    if any(r["status"] in ("MISSING", "EXPIRED") for r in assessment["requirements"]):
        return "Waiting for Documents"
    return "Applied"


def build_record(assessment: dict, account_id: str, *, job_id: str | None = None,
                 requested_by: str | None = None, request_reason: str | None = None,
                 names: dict | None = None) -> dict:
    names = names or load_names()
    f, sf = names["fields"], names["subform_fields"]
    rows = []
    proposals = sorted(assessment["proposals"],
                       key=lambda p: (p["action"] not in NEEDS_DECISION, p["zoho_field"]))
    for p in proposals[:MAX_ROWS]:
        row = {
            sf["Zoho Field"]: p["zoho_field"],
            sf["Field Label"]: FIELD_LABELS.get(p["zoho_field"],
                                                p["zoho_field"].replace("_", " ")),
            sf["Current Value"]: _text(p.get("current_value")),
            sf["Proposed Value"]: _text(p.get("proposed_value")),
            sf["Action"]: p["action"] if p["action"] in ACTION_VALUES else None,
            sf["Confidence"]: p.get("confidence"),
            sf["OCR Check"]: p.get("grounding") if p.get("grounding") in OCR_VALUES else "NONE",
            sf["Evidence"]: _evidence(p),
            sf["Auto Applied"]: AUTO_APPLIED.get(p.get("auto_apply_status"), "No"),
        }
        if p["action"] in NEEDS_DECISION and p.get("auto_apply_status") != "APPLIED":
            row[sf["Decision"]] = "Pending"
        rows.append({k: v for k, v in row.items() if v is not None})

    auto = assessment.get("auto_apply") or {}
    alerts = auto.get("alerts") or assessment.get("alerts") or []
    status = auto.get("status_decision") or assessment.get("status_decision")
    reasons = list(assessment["reasons"])
    if status:
        reasons.append(f"Customer_Status: {status['decision']} - {status['reason']}")
    confidences = [p["confidence"] for p in assessment["proposals"]
                   if p["action"] in ("FILL", "CORRECT") and p.get("confidence") is not None]
    record = {
        f["Account"]: {"id": account_id},
        f["Review Status"]: review_status(assessment),
        f["Recommendation"]: assessment["recommendation"],
        f["Recommendation Reasons"]: _text("\n".join(reasons), 2000, multiline=True),
        f["Alerts"]: _text("\n".join(f"[{a['severity']}] {a['message']}"
                                     + (f" -> {a['action']}" if a.get("action") else "")
                                     for a in alerts), 2000, multiline=True),
        f["Required Documents"]: _text("\n".join(
            f"{r['document_type']}: {r['status']}"
            + (f" (until {r['valid_until']})" if r.get("valid_until") else "")
            + (f" - {short_name(r['file'])}" if r.get("file") else "")
            for r in assessment["requirements"]), 2000, multiline=True),
        f["Documents Found"]: len(assessment.get("documents") or []),
        f["Lowest Confidence"]: min(confidences) if confidences else None,
        f["Job ID"]: _text(job_id),
        f["Rules Version"]: assessment.get("rules_version"),
        f["Requested By"]: _text(requested_by),
        f["Request Reason"]: _text(request_reason, 2000, multiline=True),
        f["Proposed Changes"]: rows,
    }
    return {k: v for k, v in record.items() if v not in (None, "")}
