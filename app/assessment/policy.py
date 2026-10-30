"""Business rules for Phase 1D. DRAFT: business owners must approve these before go-live.
Change RULES_VERSION whenever a rule changes; it is stored with every assessment."""

import re

# rules-1.0 (2026-10-22): approved by the business = rules-draft-1 unchanged, plus the
# "Corporation" value in Type of Business Organization.
RULES_VERSION = "rules-1.2"
# rules-1.2 (2026-10-28): activation also needs Payment Terms = COD, an Accounting contact with
# name, mobile and email, and TIN / Invoice Company Name / Billing Address matching the documents.
# rules-1.1 (2026-10-22): no assumed expiry dates. A document without a printed expiry date is
# "NO_EXPIRY" (shown as "No expiry date printed on this document"), unless ASSUMED_EXPIRY below
# defines a rule for its type.

# Rule book for documents whose expiry is not printed. Empty = never assume.
# Supported: {"MAYORS_PERMIT": "DEC_31_OF_ISSUE_YEAR"}  (Dec 31 of the permit/issue/paid year)
ASSUMED_EXPIRY: dict[str, str] = {}

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
    "CORPORATION": "Corporation",   # value added to the Zoho picklist (decision 2026-10-22)
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
    "Billing_Street": {"FILL": 1},          # empty address only (decision 2026-10-28)
}
AUTO_APPLY_GROUNDINGS = {"EXACT", "CROSS_CHECKED"}
# Never automatic: Account_Name, changing an address, attachments. Customer_Status may only be
# set to Active (app/assessment/alerts.py: status_decision); it is never set Inactive.


# ---------------- Activation checks (rules-1.2) ----------------
# Payment Terms (Accounts.Credit_Terms) values meaning Cash on Delivery: matched as whole words,
# case-insensitive. Production value: "Cash On Delivery" (confirmed 2026-10-28). Only COD
# accounts are verified (the button checks this first).
COD_PAYMENT_TERMS = ("COD", "CASH ON DELIVERY")
# The Accounting contact: a Contact of the Account whose "Contact of" says Accounting. Fields
# are tried in order (production "Contact of" first; sandbox has Position_Role / Department).
CONTACT_ROLE_FIELDS = ("Contact_of", "Contact_Of", "Contact_Type", "Department",
                       "Position_Role", "Contact_Category")   # Contact_of is a multi-select
ACCOUNTING_ROLE = "ACCOUNTING"
# Production's "Contact of" picklist spells it "Accouting" (field export 2026-10-29): both count.
ACCOUNTING_ROLES = ("ACCOUNTING", "ACCOUTING")
# ...or the Account's primary contact (checkbox; production: Account_Primary_Contact).
PRIMARY_CONTACT_FIELDS = ("Account_Primary_Contact", "Primary_Contact")
# (Vendor_Primary_Contact is for Vendors and does not count.)


def is_cod(payment_terms) -> bool:
    text = " ".join(str(payment_terms or "").upper().replace("-", " ").split())
    return any(re.search(rf"\b{re.escape(t)}\b", text) for t in COD_PAYMENT_TERMS)


def ph_mobile(value) -> str | None:
    """Philippine mobile number in the standard format '+63 9XX XXX XXXX', or None.
    Accepts 09XXXXXXXXX, +639XXXXXXXXX, 639XXXXXXXXX, 9XXXXXXXXX (spaces, dashes, dots ok)."""
    raw = str(value or "").strip()
    if not raw or re.search(r"[^\d\s+().-]", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("63") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or not digits.startswith("9"):
        return None
    return f"+63 {digits[:3]} {digits[3:6]} {digits[6:]}"
