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
