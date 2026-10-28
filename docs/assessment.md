# Phase 1D: customer assessment and Zoho change proposals

After extraction, the worker assesses the customer (job status `ASSESSED`):
`GET /v1/jobs/{id}/assessment`, or offline: `scripts/assess_account.py samples/<account_id>`.

## 1. Facts and cross-document checks

| Attribute | Sources (preference order) | Disagreement |
|---|---|---|
| owner_name | DTI owner, permit owner, government ID holder, BIR taxpayer (sole proprietors only) | **CRITICAL** |
| tin | BIR 2303, TIN on a government ID (first 9 digits) | **CRITICAL** |
| business_name | DTI business name, BIR trade name, permit business name | WARNING |
| registered_name | BIR taxpayer name | WARNING |
| address | BIR registered address, permit business address | INFO (stall vs residence is normal) |

Matching rules learned from real documents: `DELA CRUZ, JUAN` = `JUAN DELA CRUZ`; middle initials
ignored; a left-out middle name is LIKELY_SAME; possessives/legal suffixes ignored in business
names; address abbreviations (BLK, LT, BRGY, ST, PH...) and filler words (CITY, OF, PHILIPPINES...)
normalized; one address containing the other is SAME; **any changed number is DIFFERENT**.

## Customer Information Sheet and other data

- RGF's own **Customer Information Sheet** (.xlsx) is read directly from its cells (no OCR, no
  AI): TIN, company name, trade name, addresses, contact person and position, email, phone
  (leading 0 restored), receivers, etc. It is self-declared, so it confirms documents (e.g. TIN)
  but never outranks the BIR 2303 / DTI / permit. Its contact person counts as the owner only if
  the position says Owner.
- The AI also returns **every other labelled item** on a document (`other_fields`): telephone,
  email, BIN, eOR No., employees... Emails and phone numbers are used as contact facts.
- `document_data` in the assessment (and `extracted_data.json` from `assess_account.py`) holds
  everything read from every file, next to the Zoho values.
- Contact proposals: **Email, Phone, Contact_Person** - fill only (an existing Zoho value is
  shown as DIFFERS, never overwritten). Different emails/phones across sources are normal
  (status DIFFERS, INFO).

Other rules from real customers: a business name differing by a misread letter or two (>= 90%
similar, same numbers) is LIKELY_CONSISTENT; a required document whose validity is UNKNOWN blocks
ACTIVE.

**Expiry dates (rules-1.1):** only printed expiry dates are used. A document without one is
NO_EXPIRY ("No expiry date printed on this document", WARNING for the reviewer to check) and
counts as valid. Assumed expiries are added per document type in the rule book
`policy.ASSUMED_EXPIRY`, e.g. `{"MAYORS_PERMIT": "DEC_31_OF_ISSUE_YEAR"}` (then VALIDITY_DERIVED).
The engine re-applies the current rules at every assessment, also to cached AI results.

## 2. Proposals for Zoho

| Action | Meaning |
|---|---|
| FILL | Zoho is empty; documents give the value |
| CORRECT | Zoho differs; documents give the value (with reason, e.g. Owner_Name holds the sales rep) |
| MATCH | Zoho already agrees |
| DIFFERS | Zoho differs but is never changed automatically (Account_Name, address, line of business) |
| REVIEW_CONFLICT | Documents disagree; the reviewer chooses |
| HOLD | Documents disagree on owner/TIN: **nothing** is applied until the reviewer confirms which documents belong to the customer; suggestions stay visible |
| NO_PICKLIST_VALUE | e.g. Corporation: the Zoho picklist has no value for it |
| ATTACH | Put the latest valid document into the empty fileupload field |

Fields: Owner_Name, Tax_Identification_Number_TIN, Type_of_Business_Organization,
Invoice_Company_Name (registered name for corporations), Business_Style, Billing_Street,
Account_Name (compare only), Business_Permit / BIR_Registration_COR / Business_registration.
Confidence = best source confidence + 0.05 per agreeing document (max 0.98); below 0.6 = needs
attention.

## 3. Recommendation (DRAFT rules: `app/assessment/policy.py`, needs business sign-off)

Required documents: sole proprietorship = BIR 2303 + DTI + Mayor's permit; corporation or
partnership = BIR 2303 + SEC + Mayor's permit; unknown = BIR 2303 + Mayor's permit.

1. Critical cross-document conflict, missing required document, or critical document issue
   (e.g. BRANCH_MISMATCH) -> **MANUAL_REVIEW**
2. Otherwise an expired required document -> **INACTIVE** (`EXPIRED_REQUIRED_DOCUMENT` can be
   switched to MANUAL_REVIEW for a renewal grace period)
3. Otherwise -> **ACTIVE**

Every recommendation and proposal goes to a human reviewer (Phase 3) before Zoho is changed.

## Results on the real test customers (2026-10-07, using verified values)

| Customer | Recommendation | Main reasons |
|---|---|---|
| Meat shop, Laguna | INACTIVE | DTI and Mayor's permit expired; Owner_Name -> owner (Zoho had sales rep); TIN, business type filled |
| Bistro, Tacloban | INACTIVE | 2025 Mayor's permit expired; owner, TIN, type, invoice name filled |
| Account with permit + ID | MANUAL_REVIEW | permit and ID belong to different people (all changes on HOLD); BIR 2303 missing |
| Restaurant branch (corporation) | MANUAL_REVIEW | SEC certificate and Mayor's permit missing; brand name differs from registered name |

## Activation checks (rules-1.2)

Besides valid, consistent documents, an Account becomes **Active automatically** only if:

| Check | Passes when |
| --- | --- |
| Payment Terms is COD | `Credit_Terms` is "COD" / "Cash on Delivery" (the button also refuses non-COD accounts) |
| Accounting or primary contact | a Contact of the Account with "Contact of" (or Department / Position / Category) = Accounting, **or** marked as the primary contact (`Account_Primary_Contact` / `Primary_Contact` = true), with a name, a Philippine mobile number (09XX XXX XXXX / +63 9XX XXX XXXX; shown as +63 9XX XXX XXXX) and a real email (no placeholder) |
| TIN | the Account's TIN equals the BIR 2303 TIN (format ignored), or it is filled/corrected automatically in the same run |
| Invoice Company Name | matches the registered name (corporations) / business name, or is filled automatically |
| Billing Address | Street + City + Province + Code match the BIR 2303 address, or an empty address is filled automatically |

"Automatically" = the update passes the auto-apply rules (confidence >= 0.95, OCR-confirmed).
A check that is not met makes the recommendation MANUAL_REVIEW; the review record lists every
check (OK / AFTER UPDATE / NOT MET) under "Activation checks". The values and field names are in
`app/assessment/policy.py` (COD_PAYMENT_TERMS, CONTACT_ROLE_FIELDS, ACCOUNTING_ROLE, PRIMARY_CONTACT_FIELDS, ph_mobile).
