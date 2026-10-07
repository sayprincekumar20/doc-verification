"""Status decision (activate only) and alerts for missing/expired/problem documents.

Permission given by the business (2026-10-07): the system may set Customer_Status to Active and
update fields automatically. It must NEVER set an account Inactive; for missing, expired or
conflicting documents it alerts the team instead.
"""

from datetime import date

EXPIRING_SOON_DAYS = 30
ACTIVE_VALUE = "Active"   # Zoho Customer_Status picklist value

DOCUMENT_LABELS = {
    "BIR_2303": "BIR Form 2303 (Certificate of Registration)",
    "DTI_BN_CERT": "DTI Business Name Certificate",
    "MAYORS_PERMIT": "Mayor's / Business Permit",
    "SEC_CERT": "SEC Certificate of Registration",
    "GIS": "General Information Sheet",
    "BARANGAY_CLEARANCE": "Barangay Clearance",
    "GOVERNMENT_ID": "Government ID",
    "FOOD_SAFETY_PERMIT": "Food safety permit (NMIS/FDA)",
}


def label(document_type: str | None) -> str:
    return DOCUMENT_LABELS.get(document_type or "", (document_type or "document").replace("_", " "))


def build_alerts(assessment: dict, today: date) -> list[dict]:
    alerts = []
    for r in assessment["requirements"]:
        doc = label(r["document_type"])
        if r["status"] == "MISSING":
            alerts.append({"severity": "WARNING", "code": "MISSING_DOCUMENT",
                           "document_type": r["document_type"],
                           "message": f"Missing required document: {doc}",
                           "action": f"Collect the customer's {doc}"})
        elif r["status"] == "EXPIRED":
            days = (today - date.fromisoformat(r["valid_until"])).days if r.get(
                "valid_until") else None
            alerts.append({"severity": "CRITICAL", "code": "EXPIRED_DOCUMENT",
                           "document_type": r["document_type"],
                           "message": f"{doc} expired on {r['valid_until']}"
                                      + (f" ({days} days ago)" if days is not None else ""),
                           "action": f"Collect the renewed {doc}"})
        elif r["status"] == "VALID" and r.get("valid_until"):
            left = (date.fromisoformat(r["valid_until"]) - today).days
            if 0 <= left <= EXPIRING_SOON_DAYS:
                alerts.append({"severity": "WARNING", "code": "EXPIRING_SOON",
                               "document_type": r["document_type"],
                               "message": f"{doc} expires on {r['valid_until']} "
                                          f"(in {left} days)",
                               "action": f"Remind the customer to renew the {doc}"})
    for c in assessment["cross_document"]:
        if c["status"] == "CONFLICT" and c["severity"] == "CRITICAL":
            values = "; ".join(f"{v['type']}: {v['value']}" for v in c["values"])
            alerts.append({"severity": "CRITICAL", "code": "DOCUMENT_CONFLICT",
                           "document_type": None,
                           "message": f"Documents disagree on {c['attribute']} ({values})",
                           "action": "Check whether these documents belong to this customer"})
    for d in assessment["documents"]:
        for code in d.get("critical_issues", []):
            if code != "EXPIRED":
                alerts.append({"severity": "CRITICAL", "code": code,
                               "document_type": d["type"],
                               "message": f"{d['file']}: {code}",
                               "action": "Check the document"})
    for d in assessment.get("not_extracted", []) + assessment.get("skipped_documents", []):
        alerts.append({"severity": "WARNING", "code": "UNREADABLE_DOCUMENT",
                       "document_type": None,
                       "message": f"Could not read {d['file']} ({d['status']})",
                       "action": "Ask for a clearer photo or a supported file type"})
    review = [p["zoho_field"] for p in assessment["proposals"]
              if p["action"] in ("HOLD", "REVIEW_CONFLICT", "NO_PICKLIST_VALUE", "DIFFERS")
              or (p["action"] in ("FILL", "CORRECT") and p.get("needs_attention"))]
    if review:
        alerts.append({"severity": "INFO", "code": "FIELDS_NEED_REVIEW", "document_type": None,
                       "message": "Fields needing a person's check: " + ", ".join(review),
                       "action": None})
    return alerts


def status_decision(assessment: dict, current_status: str | None, threshold: float
                    ) -> tuple[str, str]:
    """ACTIVATE | ALREADY_ACTIVE | NO_CHANGE (never deactivates)."""
    rec = assessment["recommendation"]
    is_active = (current_status or "").strip().lower() == ACTIVE_VALUE.lower()
    if rec != "ACTIVE":
        if is_active:
            return "NO_CHANGE", (f"Recommendation is {rec} but the account is Active in Zoho. "
                                 "Not deactivated automatically: a person must decide.")
        return "NO_CHANGE", f"Recommendation is {rec}; status is not changed automatically."
    blocked = [p["zoho_field"] for p in assessment["proposals"]
               if p["action"] in ("HOLD", "REVIEW_CONFLICT")]
    if blocked:
        return "NO_CHANGE", "Unresolved document conflicts on: " + ", ".join(blocked)
    facts = assessment.get("facts", {})
    for attribute in ("owner_name", "tin"):
        best = max((f.get("confidence") or 0 for f in facts.get(attribute, [])), default=0)
        if best < threshold:
            return "NO_CHANGE", (f"{attribute} not confirmed with confidence >= {threshold} "
                                 f"(best {best}); a person must check before activating.")
    if is_active:
        return "ALREADY_ACTIVE", "Documents complete and valid; account already Active."
    return "ACTIVATE", "All required documents present, valid and consistent; owner and TIN " \
                       "confirmed."


def format_note(assessment: dict, alerts: list[dict], status: tuple[str, str],
                applied: dict[str, str]) -> tuple[str, str]:
    worst = ("CRITICAL" if any(a["severity"] == "CRITICAL" for a in alerts) else
             "WARNING" if any(a["severity"] == "WARNING" for a in alerts) else "OK")
    title = f"Document verification: {assessment['recommendation']}" + (
        f" - {worst}" if worst != "OK" else "")
    lines = [f"Recommendation: {assessment['recommendation']} "
             f"(checked {assessment['assessed_on']}, rules {assessment['rules_version']})",
             f"Status: {status[0]} - {status[1]}", ""]
    if alerts:
        lines.append("Issues:")
        lines += [f"- [{a['severity']}] {a['message']}" + (f" -> {a['action']}"
                                                          if a.get("action") else "")
                  for a in alerts]
        lines.append("")
    if applied:
        lines.append("Updated automatically: " + ", ".join(
            f"{field} ({status})" for field, status in applied.items()))
    lines.append("Required documents: " + ", ".join(
        f"{r['document_type']} {r['status']}" for r in assessment["requirements"]))
    return title, "\n".join(lines)


def alert_signature(alerts: list[dict]) -> str:
    """Same set of actionable issues -> same signature (to avoid duplicate tasks)."""
    keys = sorted(f"{a['code']}:{a.get('document_type')}" for a in alerts
                  if a["severity"] in ("CRITICAL", "WARNING"))
    return "|".join(keys)
