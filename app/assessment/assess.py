"""Consolidate document facts, check documents against each other, compare with Zoho, and
recommend a status. Pure function over extraction results: easy to test and to re-run."""

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.assessment import policy
from app.assessment.matching import (
    DIFFERENT,
    LIKELY_SAME,
    SAME,
    match_address,
    match_business,
    match_person,
    match_tin,
    natural_person_name,
)
from app.extraction.normalize import normalize_name


@dataclass
class DocInput:
    document_id: str
    file_name: str
    document_type: str
    fields: dict[str, dict]            # FieldResult dicts from extraction
    validity_status: str | None = None
    valid_until: str | None = None
    issues: list[dict] = field(default_factory=list)
    other_fields: list[dict] = field(default_factory=list)  # every other labelled item


@dataclass
class Fact:
    attribute: str
    value: str
    document_id: str
    file_name: str
    document_type: str
    source_field: str
    grounding: str | None
    confidence: float | None


# attribute -> [(document type, field)] in order of preference for the proposed value
SOURCES = {
    "owner_name": [("DTI_BN_CERT", "owner_name"), ("MAYORS_PERMIT", "owner_name"),
                   ("GOVERNMENT_ID", "full_name"), ("BIR_2303", "taxpayer_name"),
                   ("CUSTOMER_INFO_SHEET", "contact_person")],
    "business_name": [("DTI_BN_CERT", "business_name"), ("BIR_2303", "trade_name"),
                      ("MAYORS_PERMIT", "business_name"),
                      ("CUSTOMER_INFO_SHEET", "company_name")],
    "registered_name": [("BIR_2303", "taxpayer_name")],
    "tin": [("BIR_2303", "tin"), ("GOVERNMENT_ID", "tin"), ("CUSTOMER_INFO_SHEET", "tin")],
    "address": [("BIR_2303", "registered_address"), ("MAYORS_PERMIT", "business_address"),
                ("CUSTOMER_INFO_SHEET", "billing_address")],
    # contact details: the customer's own form first, then items printed on documents
    "email": [("CUSTOMER_INFO_SHEET", "email")],
    "phone": [("CUSTOMER_INFO_SHEET", "phone")],
    "contact_person": [("CUSTOMER_INFO_SHEET", "contact_person")],
    "taxpayer_type": [("BIR_2303", "taxpayer_type")],
    "line_of_business": [("BIR_2303", "line_of_business"), ("MAYORS_PERMIT", "line_of_business")],
}
_MATCHERS = {
    "owner_name": match_person,
    "business_name": match_business,
    "registered_name": match_business,
    "tin": lambda a, b: match_tin(a, b, ignore_branch=True),  # a TIN ID has no branch code
    "address": match_address,
    "email": lambda a, b: SAME if a.strip().lower() == b.strip().lower() else DIFFERENT,
    "phone": lambda a, b: SAME if _phone_key(a) == _phone_key(b) else DIFFERENT,
}
# Documents that expire: their validity must be known before an account can be ACTIVE.
EXPIRING_DOCUMENTS = {"MAYORS_PERMIT", "DTI_BN_CERT", "GOVERNMENT_ID", "BARANGAY_CLEARANCE",
                      "FOOD_SAFETY_PERMIT"}
_SEVERITY = {"owner_name": "CRITICAL", "tin": "CRITICAL", "business_name": "WARNING",
             "registered_name": "WARNING", "address": "INFO", "email": "INFO", "phone": "INFO"}
_REASONS = {
    "address": "Business address and registered address can legitimately differ "
               "(e.g. branch or stall vs owner's residence).",
    "business_name": "Trade/brand names can differ from the registered name.",
    "email": "A business can use several email addresses.",
    "phone": "A business can use several phone numbers.",
}
_EMAIL_LABEL = re.compile(r"e-?mail", re.IGNORECASE)
_PHONE_LABEL = re.compile(r"tel|phone|mobile|cell|contact no", re.IGNORECASE)


def _phone_key(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("63"):
        digits = digits[2:]
    return digits.lstrip("0")


def _is_sole_prop(docs: list[DocInput]) -> bool:
    for d in docs:
        if d.document_type == "BIR_2303":
            t = (d.fields.get("taxpayer_type") or {}).get("value") or ""
            return "PROPRIETOR" in t.upper() or "INDIVIDUAL" in t.upper()
    return True  # DTI/permit-only accounts are sole proprietors in practice


def collect_facts(docs: list[DocInput]) -> dict[str, list[Fact]]:
    sole_prop = _is_sole_prop(docs)
    facts: dict[str, list[Fact]] = {a: [] for a in SOURCES}
    for attribute, sources in SOURCES.items():
        for doc_type, field_name in sources:
            if attribute == "owner_name" and doc_type == "BIR_2303" and not sole_prop:
                continue  # a corporation's taxpayer name is not a person
            for d in docs:
                f = d.fields.get(field_name) if d.document_type == doc_type else None
                if (attribute == "owner_name" and doc_type == "CUSTOMER_INFO_SHEET" and f
                        and "OWNER" not in str((d.fields.get("contact_position") or {})
                                               .get("value", "")).upper()):
                    continue  # the contact person is the owner only if the form says so
                if f and f.get("value"):
                    facts[attribute].append(Fact(
                        attribute, f["value"], d.document_id, d.file_name, d.document_type,
                        field_name, f.get("grounding"), f.get("confidence")))
    # Items printed on documents outside the predefined fields (e.g. the permit's telephone)
    for d in docs:
        for o in d.other_fields:
            label, value = str(o.get("label", "")), str(o.get("value", "")).strip()
            attribute = ("email" if _EMAIL_LABEL.search(label) and "@" in value else
                         "phone" if _PHONE_LABEL.search(label) and _phone_key(value) else None)
            if attribute:
                facts[attribute].append(Fact(attribute, value, d.document_id, d.file_name,
                                             d.document_type, f"other: {label}", None, 0.8))
    return facts


def cross_check(facts: dict[str, list[Fact]]) -> list[dict]:
    results = []
    for attribute, matcher in _MATCHERS.items():
        items = facts.get(attribute, [])
        if len(items) < 2:
            continue
        pairs, worst = [], SAME
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a.document_id == b.document_id:
                    continue
                verdict = matcher(a.value, b.value) or SAME
                pairs.append({"a": f"{a.document_type}:{a.value}",
                              "b": f"{b.document_type}:{b.value}", "result": verdict})
                if verdict == DIFFERENT or (verdict == LIKELY_SAME and worst == SAME):
                    worst = verdict
        if not pairs:
            continue
        status = {SAME: "CONSISTENT", LIKELY_SAME: "LIKELY_CONSISTENT",
                  DIFFERENT: "CONFLICT"}[worst]
        if status == "CONFLICT" and _SEVERITY[attribute] == "INFO":
            status = "DIFFERS"  # normal differences (several emails, branch vs residence)
        results.append({
            "attribute": attribute, "status": status,
            "severity": _SEVERITY[attribute] if status == "CONFLICT" else "INFO",
            "values": [{"document": f.file_name, "type": f.document_type, "value": f.value,
                        "confidence": f.confidence} for f in items],
            "pairs": pairs,
            "note": _REASONS.get(attribute) if status in ("CONFLICT", "DIFFERS") else None,
        })
    return results


def _best(facts: list[Fact]) -> Fact | None:
    """First source in preference order whose value is reasonably confident."""
    good = [f for f in facts if (f.confidence or 0) >= policy.MIN_CONFIDENCE_FOR_PROPOSAL]
    return (good or facts or [None])[0]


def _confidence(chosen: Fact, facts: list[Fact], matcher) -> float:
    agreeing = [f for f in facts if f.document_id != chosen.document_id
                and matcher(chosen.value, f.value) in (SAME, LIKELY_SAME)]
    base = chosen.confidence or 0.5
    return round(min(0.98, base + 0.05 * len(agreeing)), 2)


def _proposal(zoho_field, current, proposed, action, confidence, sources, reason,
              attention=False, grounding=None, agreeing=0) -> dict:
    return {"zoho_field": zoho_field, "current_value": current, "proposed_value": proposed,
            "action": action, "confidence": confidence, "sources": sources, "reason": reason,
            "grounding": grounding,           # how the chosen value was confirmed
            "agreeing_documents": agreeing,   # documents (incl. the chosen one) with this value
            "needs_attention": attention or (confidence is not None
                                             and confidence < policy.MIN_CONFIDENCE_FOR_PROPOSAL)}


def _src(f: Fact) -> dict:
    return {"document": f.file_name, "type": f.document_type, "field": f.source_field,
            "value": f.value, "grounding": f.grounding, "confidence": f.confidence}


def _compare_field(zoho_field: str, current: Any, chosen: Fact | None, proposed: str | None,
                   matcher, facts: list[Fact], conflict: bool, *, allow_correct: bool = True,
                   extra_reason: str = "") -> dict | None:
    if chosen is None or not proposed:
        return None
    sources = [_src(f) for f in facts]
    conf = _confidence(chosen, facts, matcher)
    agreeing = 1 + sum(1 for f in facts if f.document_id != chosen.document_id
                       and matcher(chosen.value, f.value) in (SAME, LIKELY_SAME))
    extra = {"grounding": chosen.grounding, "agreeing": agreeing}
    if conflict:
        return _proposal(zoho_field, current, None, "REVIEW_CONFLICT", None, sources,
                         "Documents disagree on this value; the reviewer must choose.", True)
    if current in (None, "", []):
        return _proposal(zoho_field, current, proposed, "FILL", conf, sources,
                         "Zoho is empty; value found on documents.", **extra)
    verdict = matcher(str(current), proposed)
    if verdict in (SAME, LIKELY_SAME):
        # Zoho and the document are independent sources; agreement confirms the value even
        # when OCR could not (low document confidence), so nothing needs a person's attention.
        p = _proposal(zoho_field, current, current, "MATCH", conf, sources,
                      "Zoho matches the documents." if verdict == SAME else
                      "Zoho matches the documents (minor wording difference).", **extra)
        p["needs_attention"] = False
        return p
    if not allow_correct:
        return _proposal(zoho_field, current, None, "DIFFERS", conf, sources,
                         "Zoho differs from the documents; not changed automatically. "
                         + extra_reason, True, **extra)
    return _proposal(zoho_field, current, proposed, "CORRECT", conf, sources,
                     ("Zoho differs from the documents. " + extra_reason).strip(), **extra)


def assess(snapshot: dict, docs: list[DocInput], today: date) -> dict:
    facts = collect_facts(docs)
    checks = cross_check(facts)
    conflict = {c["attribute"] for c in checks if c["status"] == "CONFLICT"
                and c["severity"] in ("CRITICAL", "WARNING")}
    snap = snapshot or {}
    proposals: list[dict] = []

    # Owner_Name (person). Zoho often holds the CRM record owner (sales rep) here.
    owner = _best(facts["owner_name"])
    if owner:
        note = ""
        record_owner = (snap.get("Owner") or {}).get("name") if isinstance(
            snap.get("Owner"), dict) else None
        if record_owner and match_person(snap.get("Owner_Name"), record_owner) == SAME:
            note = (f"Zoho Owner_Name equals the CRM record owner ({record_owner}), "
                    "the sales rep, not the business owner.")
        p = _compare_field("Owner_Name", snap.get("Owner_Name"), owner,
                           natural_person_name(owner.value), match_person, facts["owner_name"],
                           "owner_name" in conflict, extra_reason=note)
        if p:
            proposals.append(p)

    # TIN (BIR 2303 is authoritative; TIN ID can only fill the 9-digit TIN)
    tin = _best(facts["tin"])
    if tin:
        tin_value = (next((d.fields["tin"].get("normalized") for d in docs
                           if d.document_id == tin.document_id), None) or tin.value)
        p = _compare_field("Tax_Identification_Number_TIN", snap.get(
            "Tax_Identification_Number_TIN"), tin, tin_value, match_tin, facts["tin"],
            "tin" in conflict)
        if p:
            proposals.append(p)

    # Type of business organization (picklist)
    ttype = _best(facts["taxpayer_type"])
    form = policy.business_form(ttype.value if ttype else None,
                                snap.get("Type_of_Business_Organization"))
    if ttype:
        zoho_value = policy.BUSINESS_FORM_TO_ZOHO.get(form)
        if zoho_value:
            p = _compare_field("Type_of_Business_Organization",
                               snap.get("Type_of_Business_Organization"), ttype, zoho_value,
                               lambda a, b: SAME if policy.business_form(a, None) ==
                               policy.business_form(b, None) else DIFFERENT,
                               facts["taxpayer_type"], False)
            if p:
                proposals.append(p)
        else:
            proposals.append(_proposal(
                "Type_of_Business_Organization", snap.get("Type_of_Business_Organization"),
                None, "NO_PICKLIST_VALUE", None, [_src(ttype)],
                f"Documents say {ttype.value}; the Zoho picklist has no matching value.", True))

    # Invoice company name: registered name for corporations, business name otherwise
    name_attr = "registered_name" if form in ("CORPORATION", "PARTNERSHIP") else "business_name"
    name = _best(facts[name_attr])
    if name:
        p = _compare_field("Invoice_Company_Name", snap.get("Invoice_Company_Name"), name,
                           " ".join(name.value.split()), match_business, facts[name_attr],
                           name_attr in conflict)
        if p:
            proposals.append(p)

    # Line of business -> Business_Style (fill only)
    lob = _best(facts["line_of_business"])
    if lob:
        p = _compare_field("Business_Style", snap.get("Business_Style"), lob, lob.value,
                           lambda a, b: SAME if normalize_name(a) == normalize_name(b)
                           else DIFFERENT, [lob], False, allow_correct=False)
        if p:
            proposals.append(p)

    # Address -> Billing_Street (fill only; Zoho splits street/city/province)
    addr = _best(facts["address"])
    if addr:
        p = _compare_field("Billing_Street", snap.get("Billing_Street"), addr, addr.value,
                           match_address, [addr], False, allow_correct=False,
                           extra_reason="Addresses are split into several Zoho fields.")
        if p:
            proposals.append(p)

    # Contact details -> Zoho (fill only; a different value is shown, never overwritten)
    for attribute, zoho_field, matcher in (
            ("email", "Email", _MATCHERS["email"]), ("phone", "Phone", _MATCHERS["phone"]),
            ("contact_person", "Contact_Person", match_person)):
        chosen = _best(facts[attribute])
        if chosen:
            p = _compare_field(zoho_field, snap.get(zoho_field), chosen, chosen.value,
                               lambda a, b, m=matcher: m(a, b) or DIFFERENT,
                               facts[attribute], False, allow_correct=False)
            if p:
                proposals.append(p)

    # Account name: compare only, never changed automatically
    biz = _best(facts["business_name"]) or _best(facts["registered_name"])
    if biz and snap.get("Account_Name"):
        verdict = max((match_business(snap["Account_Name"], f.value) or DIFFERENT
                       for f in facts["business_name"] + facts["registered_name"]),
                      key=[DIFFERENT, LIKELY_SAME, SAME].index)
        proposals.append(_proposal(
            "Account_Name", snap["Account_Name"], None,
            "MATCH" if verdict != DIFFERENT else "DIFFERS", None,
            [_src(f) for f in facts["business_name"] + facts["registered_name"]],
            "Account name matches a name on the documents." if verdict != DIFFERENT else
            "Account name differs from the names on the documents (brand vs registered "
            "name, or documents of another business?). Not changed automatically.",
            verdict == DIFFERENT))

    # Requirements and validity
    required = policy.REQUIRED_DOCUMENTS[form]
    by_type: dict[str, list[DocInput]] = {}
    for d in docs:
        by_type.setdefault(d.document_type, []).append(d)
    requirements = []
    for doc_type in required:
        candidates = sorted(by_type.get(doc_type, []), key=lambda d: (
            d.validity_status in ("VALID", "NO_EXPIRY"), d.valid_until or ""), reverse=True)
        best = candidates[0] if candidates else None
        status = "MISSING" if best is None else (best.validity_status or "UNKNOWN")
        requirements.append({"document_type": doc_type, "status": status,
                             "file": best.file_name if best else None,
                             "valid_until": best.valid_until if best else None})

    # File attachments for empty fileupload fields
    for doc_type, zoho_field in policy.FILE_FIELDS.items():
        candidates = [d for d in by_type.get(doc_type, [])
                      if d.validity_status in ("VALID", "NO_EXPIRY", "UNKNOWN")]
        if candidates and not snap.get(zoho_field):
            best = sorted(candidates, key=lambda d: d.valid_until or "", reverse=True)[0]
            proposals.append(_proposal(
                zoho_field, None, best.file_name, "ATTACH", None,
                [{"document": best.file_name, "type": doc_type}],
                f"Attach the {doc_type.replace('_', ' ').title()} to this field."))

    # Documents disagree on who the customer is: nothing from them may be applied until a
    # person decides which documents belong to this account. Suggestions stay visible.
    critical = [c["attribute"] for c in checks
                if c["status"] == "CONFLICT" and c["severity"] == "CRITICAL"]
    if critical:
        for p in proposals:
            if p["action"] in ("FILL", "CORRECT", "ATTACH"):
                p["action"], p["needs_attention"] = "HOLD", True
                p["reason"] = (f"On hold: documents disagree on {', '.join(critical)}. Confirm "
                               "which documents belong to this customer first. " + p["reason"])

    recommendation, reasons = _recommend(checks, requirements, docs)
    return {
        "rules_version": policy.RULES_VERSION, "assessed_on": today.isoformat(),
        "business_form": form, "recommendation": recommendation, "reasons": reasons,
        "requirements": requirements, "cross_document": checks, "proposals": proposals,
        "facts": {a: [asdict(f) for f in fs] for a, fs in facts.items() if fs},
        # Everything read from every file, as JSON: the fields and every other labelled item.
        "document_data": [{"file": d.file_name, "type": d.document_type,
                           "validity": d.validity_status, "valid_until": d.valid_until,
                           "fields": {k: (v or {}).get("value") for k, v in d.fields.items()
                                      if (v or {}).get("value") not in (None, "")},
                           "other_fields": [{"label": o.get("label"), "value": o.get("value")}
                                            for o in d.other_fields]} for d in docs],
        "documents": [{"file": d.file_name, "type": d.document_type,
                       "validity": d.validity_status, "valid_until": d.valid_until,
                       "critical_issues": [i["code"] for i in d.issues
                                           if i.get("severity") == "CRITICAL"]} for d in docs],
    }


def _recommend(checks, requirements, docs) -> tuple[str, list[str]]:
    reasons_review, reasons_inactive = [], []
    for c in checks:
        if c["status"] == "CONFLICT" and c["severity"] == "CRITICAL":
            values = " vs ".join(f"{v['value']} ({v['type']})" for v in c["values"])
            reasons_review.append(f"Documents disagree on {c['attribute']}: {values}")
    for r in requirements:
        if r["status"] == "MISSING":
            reasons_review.append(f"Required document missing: {r['document_type']}")
        elif r["status"] == "UNKNOWN" and r["document_type"] in EXPIRING_DOCUMENTS:
            reasons_review.append(f"Validity of required document could not be determined: "
                                  f"{r['document_type']} (check its expiry date)")
        elif r["status"] == "EXPIRED":
            msg = f"Required document expired: {r['document_type']} (until {r['valid_until']})"
            (reasons_inactive if policy.EXPIRED_REQUIRED_DOCUMENT == "INACTIVE"
             else reasons_review).append(msg)
    for d in docs:
        for i in d.issues:
            if i.get("severity") == "CRITICAL" and i.get("code") != "EXPIRED":
                reasons_review.append(f"{d.file_name}: {i['code']} - {i['message']}")
    if reasons_review:
        return "MANUAL_REVIEW", reasons_review + reasons_inactive
    if reasons_inactive:
        return "INACTIVE", reasons_inactive
    return "ACTIVE", ["All required documents present and valid; documents agree."]
