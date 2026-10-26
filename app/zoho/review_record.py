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
        "Reviewer": "Reviewer", "Reviewed Time": "Reviewed_Time",
        "Reviewer Notes": "Reviewer_Notes", "Values Compared": "Values_Compared",
        "Activate Account": "Activate_Account",
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
    "Email": "Email", "Phone": "Phone",
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
        s = "\n".join((line[:len(line) - len(line.lstrip())] + " ".join(line.split()))
                      if line.strip() else "" for line in s.splitlines())
        s = re.sub(r"\n{3,}", "\n\n", s).strip("\n")
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


# ---------- readable text for Alerts / Recommendation Reasons / Required Documents ----------
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WORDS = {"owner_name": "owner name", "business_name": "business name", "registered_name":
          "registered name", "tin": "TIN", "Customer_Status": "Customer Status"}
_SEVERITY = {"CRITICAL": "URGENT", "WARNING": "ATTENTION", "INFO": "NOTE"}


def _date(iso: str | None) -> str:
    m = _ISO_DATE.fullmatch(iso or "")
    return f"{int(m.group(3))} {_MONTHS[int(m.group(2)) - 1]} {m.group(1)}" if m else (iso or "")


def humanize(text: str) -> str:
    """Document codes -> names, ISO dates -> '31 Dec 2025', field codes -> words."""
    from app.assessment.alerts import DOCUMENT_LABELS
    for code, label in DOCUMENT_LABELS.items():
        text = re.sub(rf"\b{code}\b", label, text)
    text = _ISO_DATE.sub(lambda m: _date(m.group(0)), text)
    for code, word in _WORDS.items():
        text = re.sub(rf"\b{code}\b", word, text)
    return text.replace(" -> ", ": ")


def format_alerts(alerts: list[dict]) -> str:
    lines = []
    for i, a in enumerate(alerts, 1):
        lines.append(f"{i}. {_SEVERITY.get(a.get('severity'), a.get('severity'))}: "
                     f"{humanize(str(a.get('message', '')))}")
        if a.get("action"):
            lines.append(f"   Action: {humanize(str(a['action']))}")
    return "\n".join(lines)


def format_requirements(requirements: list[dict]) -> str:
    from app.assessment.alerts import label
    lines = []
    for r in requirements:
        status, until = r.get("status"), r.get("valid_until")
        if status == "VALID":
            text = f"Valid until {_date(until)}" if until else "Valid"
        elif status == "EXPIRED":
            text = f"EXPIRED on {_date(until)}"
        elif status == "MISSING":
            text = "MISSING (not uploaded)"
        elif status == "NO_EXPIRY":
            text = "Valid (" + (r.get("valid_until_source") or "no expiry date") + ")"
        elif status == "NOT_YET_VALID":
            text = "Not yet valid"
        else:
            text = "Expiry date could not be determined"
        file = f"\n   File: {short_name(r['file'])}" if r.get("file") else ""
        lines.append(f"- {label(r['document_type'])}: {text}{file}")
    return "\n".join(lines)


def format_reasons(recommendation: str, reasons: list[str], status: dict | None) -> str:
    lines = [f"Recommendation: {recommendation.replace('_', ' ')}", "Why:"]
    lines += [f"{i}. {humanize(r)}" for i, r in enumerate(reasons, 1)]
    if status:
        what = {"ACTIVATE": "set to Active automatically",
                "ALREADY_ACTIVE": "already Active (no change)"}.get(status["decision"],
                                                                     "not changed")
        lines += ["", f"Customer Status: {what}. {humanize(status['reason'])}"]
    return "\n".join(lines)


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
    confidences = [p["confidence"] for p in assessment["proposals"]
                   if p["action"] in ("FILL", "CORRECT") and p.get("confidence") is not None]
    record = {
        f["Account"]: {"id": account_id},
        f["Review Status"]: review_status(assessment),
        f["Recommendation"]: assessment["recommendation"],
        f["Recommendation Reasons"]: _text(format_reasons(
            assessment["recommendation"], assessment["reasons"], status), 2000, multiline=True),
        f["Alerts"]: _text(format_alerts(alerts), 2000, multiline=True),
        f["Required Documents"]: _text(format_requirements(assessment["requirements"]), 2000,
                                       multiline=True),
        f["Documents Found"]: len(assessment.get("documents") or []),
        f["Lowest Confidence"]: min(confidences) if confidences else None,
        f["Job ID"]: _text(job_id),
        f["Rules Version"]: assessment.get("rules_version"),
        f["Requested By"]: _text(requested_by),
        f["Request Reason"]: _text(request_reason, 2000, multiline=True),
        f["Proposed Changes"]: rows,
    }
    return {k: v for k, v in record.items() if v not in (None, "")}
