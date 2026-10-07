"""What to extract from each Philippine document type.

`kind` drives normalization and validation. `zoho_field` is the Account field a value may
fill or correct later (Phase 1D proposals); None means it's used for checks only.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    description: str
    kind: str = "text"   # text | name | address | date | tin | number | money | code | choice
    required: bool = False
    zoho_field: str | None = None
    options: tuple[str, ...] = ()   # for kind="choice" (printed checkboxes)


SPECS: dict[str, list[FieldSpec]] = {
    "BIR_2303": [
        FieldSpec("tin", "TIN & Branch Code, e.g. 601-088-612-00000", "tin", True,
                  "Tax_Identification_Number_TIN"),
        FieldSpec("taxpayer_name", "NAME OF TAXPAYER exactly as printed", "name", True),
        FieldSpec("trade_name", "TRADE NAME 1 in Business Information Details", "name", False,
                  "Invoice_Company_Name"),
        FieldSpec("registered_address", "REGISTERED ADDRESS", "address", True, "Billing_Street"),
        FieldSpec("taxpayer_type", "TAXPAYER TYPE/S, e.g. SINGLE PROPRIETORSHIP ONLY "
                  "(RESIDENT CITIZEN) or DOMESTIC CORPORATION", "text", True,
                  "Type_of_Business_Organization"),
        FieldSpec("registering_office", "Which REGISTERING OFFICE box is marked with X: "
                  "Head Office or Branch", "choice", options=("Head Office", "Branch")),
        FieldSpec("rdo_code", "Revenue District Office number, 3 digits, e.g. 056", "code"),
        FieldSpec("tin_issuance_date", "TIN ISSUANCE DATE", "date"),
        FieldSpec("registration_date", "REGISTRATION DATE in Business Information Details",
                  "date"),
        FieldSpec("line_of_business", "Line of Business", "text", False, "Business_Style"),
        FieldSpec("psic", "PSIC code and description, e.g. 56101-RESTAURANTS", "text"),
        FieldSpec("ocn", "OCN number at the top right", "code"),
    ],
    "DTI_BN_CERT": [
        FieldSpec("business_name", "The registered business name", "name", True,
                  "Invoice_Company_Name"),
        FieldSpec("owner_name", "Person the certificate is issued to", "name", True,
                  "Owner_Name"),
        FieldSpec("bn_number", "Business Name No.", "code", True),
        FieldSpec("valid_from", "Start of validity ('valid from ...')", "date", True),
        FieldSpec("valid_to", "End of validity ('... to ...')", "date", True),
        FieldSpec("territorial_scope", "Scope under the business name, e.g. CITY/MUNICIPALITY",
                  "text"),
        FieldSpec("location", "Location line, e.g. BAY, LAGUNA - REGION IV-A (CALABARZON)",
                  "address"),
    ],
    "MAYORS_PERMIT": [
        FieldSpec("business_name", "Business Name", "name", True, "Invoice_Company_Name"),
        FieldSpec("owner_name", "Proprietor/Owner or Taxpayer's Name", "name", True,
                  "Owner_Name"),
        FieldSpec("business_address", "Business Address", "address", False, "Billing_Street"),
        FieldSpec("permit_number", "Business/Mayor's Permit No.", "code", True,
                  None),
        FieldSpec("permit_year", "Year the permit covers, e.g. 2025 ('Series of 2025')",
                  "number", True),
        FieldSpec("line_of_business", "Line of Business / Nature of Business", "text", False,
                  "Business_Style"),
        FieldSpec("issuing_lgu", "City or municipality that issued it", "text"),
        FieldSpec("or_number", "Official Receipt (O.R.) number", "code"),
        FieldSpec("amount_paid", "Total tax/amount paid", "money"),
        FieldSpec("date_paid", "Date paid", "date"),
        FieldSpec("date_issued", "Date issued", "date"),
        FieldSpec("valid_until", "Expiry date; Mayor's permits usually end December 31 of the "
                  "permit year", "date"),
        FieldSpec("business_id", "Business ID / Account No. if printed", "code"),
    ],
}

GENERIC = [
    FieldSpec("document_title", "Main title of the document", "text", True),
    FieldSpec("business_name", "Business or company name", "name"),
    FieldSpec("person_name", "Main person named (owner, holder)", "name"),
    FieldSpec("document_number", "Main registration/permit/ID number", "code"),
    FieldSpec("issue_date", "Date issued", "date"),
    FieldSpec("expiry_date", "Expiry / valid until date", "date"),
]


def specs_for(document_type: str) -> list[FieldSpec]:
    return SPECS.get(document_type, GENERIC)
