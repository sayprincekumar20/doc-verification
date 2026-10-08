"""The Zoho CRM review module ("Document Verifications") the engine writes to.

Single source of truth for the setup checklist (docs/zoho-review-module.md), the checker
(scripts/check_zoho_setup.py) and, later, the engine code that creates review records.
Fields are matched by label; Zoho generates the API names (the checker prints them).
"""

from dataclasses import dataclass

MODULE_PLURAL = "Document Verifications"
MODULE_SINGULAR = "Document Verification"
SUBFORM_LABEL = "Proposed Changes"

# Zoho data_type values accepted for each kind
TYPES = {
    "autonumber": {"autonumber"},
    "lookup": {"lookup"},
    "userlookup": {"userlookup", "ownerlookup"},
    "picklist": {"picklist"},
    "text": {"text"},
    "textarea": {"textarea"},
    "text_or_textarea": {"text", "textarea"},
    "integer": {"integer", "bigint"},
    "decimal": {"double", "decimal"},
    "datetime": {"datetime"},
    "boolean": {"boolean"},
    "subform": {"subform"},
}


@dataclass(frozen=True)
class FieldDef:
    label: str
    kind: str
    picklist: tuple[str, ...] = ()
    lookup_module: str | None = None
    note: str = ""


REVIEW_STATUSES = ("Pending Review", "In Review", "Waiting for Documents", "Approved",
                   "Applied", "Rejected", "Conflict", "Failed")

MODULE_FIELDS = [
    FieldDef("DV Number", "autonumber",
             note="The module's name field: Auto-Number, prefix DV (letters/numbers only), "
                  "start 1"),
    FieldDef("Account", "lookup", lookup_module="Accounts"),
    FieldDef("Review Status", "picklist", REVIEW_STATUSES, note="Default: Pending Review"),
    FieldDef("Recommendation", "picklist", ("ACTIVE", "INACTIVE", "MANUAL_REVIEW")),
    FieldDef("Recommendation Reasons", "textarea"),
    FieldDef("Alerts", "textarea"),
    FieldDef("Required Documents", "textarea"),
    FieldDef("Documents Found", "integer"),
    FieldDef("Lowest Confidence", "decimal", note="2 decimal places"),
    FieldDef("Job ID", "text"),
    FieldDef("Rules Version", "text"),
    FieldDef("Requested By", "text"),
    FieldDef("Request Reason", "textarea"),
    FieldDef("Reviewer", "userlookup"),
    FieldDef("Reviewed Time", "datetime"),
    FieldDef("Reviewer Notes", "textarea"),
    FieldDef("Values Compared", "boolean",
             note="Checkbox: 'I compared every approved value with the source document'"),
    FieldDef(SUBFORM_LABEL, "subform"),
]

SUBFORM_FIELDS = [
    FieldDef("Zoho Field", "text", note="API name of the Account field, e.g. Owner_Name. "
             "Create the subform with this first column; the setup script adds the rest"),
    FieldDef("Field Label", "text"),
    FieldDef("Current Value", "text"),
    FieldDef("Proposed Value", "text", note="Reviewer may edit"),
    FieldDef("Action", "picklist", ("FILL", "CORRECT", "MATCH", "HOLD", "REVIEW_CONFLICT",
                                    "DIFFERS", "ATTACH", "NO_PICKLIST_VALUE")),
    FieldDef("Confidence", "decimal", note="2 decimal places"),
    FieldDef("OCR Check", "picklist", ("EXACT", "FUZZY", "CROSS_CHECKED", "NOT_FOUND",
                                       "CONFLICT", "UNVERIFIABLE", "NONE")),
    FieldDef("Evidence", "text_or_textarea", note="Source document and evidence text"),
    FieldDef("Auto Applied", "picklist", ("No", "Applied", "Shadow", "Skipped", "Failed")),
    FieldDef("Decision", "picklist", ("Pending", "Approve", "Edit", "Reject"),
             note="Default: Pending"),
    FieldDef("Reviewer Comment", "text"),
]


RELATED_LIST_LABEL = MODULE_PLURAL   # how the module appears as a related list on Accounts


def field_payload(fd: FieldDef, module_ids: dict[str, str] | None = None) -> dict:
    """Zoho Create Custom Field API body for one field (docs: create-custom-field, v8)."""
    body: dict = {"field_label": fd.label}
    kind = fd.kind
    if kind == "text":
        body.update(data_type="text", length=255)
    elif kind in ("textarea", "text_or_textarea"):
        body.update(data_type="textarea", length=2000, textarea={"type": "small"})
    elif kind == "integer":
        body.update(data_type="integer", length=9)
    elif kind == "decimal":
        body.update(data_type="double", length=16, decimal_place=2)
    elif kind == "datetime":
        body.update(data_type="datetime")
    elif kind == "boolean":
        body.update(data_type="boolean")
    elif kind == "picklist":
        body.update(data_type="picklist", pick_list_values=[
            {"display_value": v, "actual_value": v} for v in fd.picklist])
    elif kind == "userlookup":
        body.update(data_type="userlookup")
    elif kind == "lookup":
        module = {"api_name": fd.lookup_module}
        if module_ids and fd.lookup_module in module_ids:
            module["id"] = module_ids[fd.lookup_module]
        body.update(data_type="lookup",
                    lookup={"module": module, "display_label": RELATED_LIST_LABEL})
    else:
        raise ValueError(f"{fd.label}: {kind} fields are not created by the setup script")
    return body


def module_payload(profile_ids: list[str]) -> dict:
    """Zoho Create Custom Module API body, with DV Number as the auto-number name field."""
    return {"modules": [{
        "plural_label": MODULE_PLURAL,
        "singular_label": MODULE_SINGULAR,
        "profiles": [{"id": pid} for pid in profile_ids],
        "display_field": {"field_label": "DV Number", "data_type": "autonumber",
                          "auto_number": {"prefix": "DV", "start_number": 1}},
    }]}
