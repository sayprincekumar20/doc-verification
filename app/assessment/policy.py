"""Business rules for Phase 1D. DRAFT: business owners must approve these before go-live.
Change RULES_VERSION whenever a rule changes; it is stored with every assessment."""

RULES_VERSION = "rules-draft-1"

# Documents required per business form (from BIR taxpayer type, else Zoho business type).
REQUIRED_DOCUMENTS = {
    "SOLE_PROPRIETORSHIP": ["BIR_2303", "DTI_BN_CERT", "MAYORS_PERMIT"],
    "CORPORATION": ["BIR_2303", "SEC_CERT", "MAYORS_PERMIT"],
    "PARTNERSHIP": ["BIR_2303", "SEC_CERT", "MAYORS_PERMIT"],
    "UNKNOWN": ["BIR_2303", "MAYORS_PERMIT"],
}

# What happens when a required document has expired: "INACTIVE" (per the HLD) or
# "MANUAL_REVIEW" (e.g. to allow a renewal grace period).
EXPIRED_REQUIRED_DOCUMENT = "INACTIVE"

# Zoho picklist values for Type_of_Business_Organization. The org's picklist has no
# corporation value; None = don't propose, the reviewer decides.
BUSINESS_FORM_TO_ZOHO = {
    "SOLE_PROPRIETORSHIP": "Single Proprietorship",
    "PARTNERSHIP": "Partnership",
    "CORPORATION": None,
}

# Zoho fileupload fields filled with the document of this type (latest valid one).
FILE_FIELDS = {
    "MAYORS_PERMIT": "Business_Permit",
    "BIR_2303": "BIR_Registration_COR",
    "DTI_BN_CERT": "Business_registration",
    "SEC_CERT": "Business_registration",
    "GIS": "General_Information_Sheet",
}

MIN_CONFIDENCE_FOR_PROPOSAL = 0.6     # below this a proposal is marked "needs attention"
CRITICAL_ATTRIBUTES = {"owner_name", "tin"}   # disagreement between documents = CRITICAL


def business_form(taxpayer_type: str | None, zoho_type: str | None) -> str:
    text = (taxpayer_type or zoho_type or "").upper()
    if "PROPRIETOR" in text or "INDIVIDUAL" in text:
        return "SOLE_PROPRIETORSHIP"
    if "CORPORATION" in text or "CORP" in text:
        return "CORPORATION"
    if "PARTNERSHIP" in text or "PARNERSHIP" in text:  # the Zoho picklist has this typo
        return "PARTNERSHIP"
    return "UNKNOWN"


# ---------------- Automatic updates (AUTO_APPLY_MODE = off | shadow | on) ----------------
# A proposal is written to Zoho without review only if ALL of these hold:
#   - the field and action are listed here, with at least N documents agreeing on the value
#   - confidence >= AUTO_APPLY_THRESHOLD (setting, default 0.95)
#   - the value was confirmed: grounding in AUTO_APPLY_GROUNDINGS
#   - it is not on HOLD / needing attention, and the assessment has no critical problem
#     other than an expired document
# Evidence (2026-10-07 benchmark, 101 fields): every wrong value had confidence <= 0.5.
AUTO_APPLY_RULES: dict[str, dict[str, int]] = {
    "Tax_Identification_Number_TIN": {"FILL": 1, "CORRECT": 2},
    "Type_of_Business_Organization": {"FILL": 1},
    "Business_Style": {"FILL": 1},
    "Owner_Name": {"FILL": 1, "CORRECT": 2},
    "Invoice_Company_Name": {"FILL": 1, "CORRECT": 2},
}
AUTO_APPLY_GROUNDINGS = {"EXACT", "CROSS_CHECKED"}
# Never automatic: Account_Name, addresses, attachments, Customer_Status.
