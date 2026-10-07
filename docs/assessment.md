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
