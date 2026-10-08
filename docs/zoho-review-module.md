# Zoho CRM review module: setup checklist

Build this in the **sandbox** first. Field **labels, types and picklist values must match exactly**:
the engine and `scripts/check_zoho_setup.py` find fields by label and check every picklist value.
(This file is generated from `app/zoho/review_module.py`.)

## 1. Create the sandbox

Setup (gear) -> Developer Hub / Developer Space -> **Sandbox** -> Create Sandbox. Name:
`DocVerification-Test`. Copy configuration (metadata); include sample Accounts if your plan allows.
Open the sandbox (a banner shows you are in the sandbox).

## Faster: let the script create it

Instead of steps 2-4 by hand:

1. Token with setup scopes: `python scripts/zoho_auth.py scopes --setup` -> new grant code in the
   API Console (as an admin) -> `python scripts/zoho_auth.py exchange-code --env .env.sandbox`
2. Dry run, then create:

```cmd
python scripts/create_zoho_review_module.py --env .env.sandbox
python scripts/create_zoho_review_module.py --env .env.sandbox --apply
```

3. In Zoho add the subform (Zoho's API can't create subforms): Setup > Modules and Fields >
   Document Verifications > Standard layout > drag **Subform** in, name it **Proposed Changes**,
   one Single Line column **Zoho Field**, Save.
4. Run `--apply` again: it adds the other 10 subform columns. Then step 5 (list views) by hand and
   step 7 (checker).

The script only creates what is missing (safe to re-run) and refuses non-sandbox domains.

## 2. Create the module

Setup -> Customization -> **Modules and Fields** -> **Create New Module**.
- Plural name: **Document Verifications**
- Singular name: **Document Verification**

Zoho adds a mandatory name field ("Document Verification Name"): change it to an **Auto-Number**
field labelled **DV Number** (prefix `DV`, start 1), or keep it and add DV Number separately.

## 3. Fields (drag from the left panel into the layout)

Suggested sections: **Summary** (DV Number, Account, Review Status, Recommendation, Documents Found,
Lowest Confidence), **Warnings & Alerts** (Recommendation Reasons, Alerts, Required Documents),
**Request** (Requested By, Request Reason, Job ID, Rules Version), **Review** (Reviewer, Reviewed
Time, Reviewer Notes, Values Compared).

| Label | Field type | Settings |
|---|---|---|
| DV Number | Auto-Number | The module's name field: Auto-Number, prefix DV (letters/numbers only), start 1 |
| Account | Lookup | lookup module: **Accounts** |
| Review Status | Pick List | values (exactly, in this order): `Pending Review`, `In Review`, `Waiting for Documents`, `Approved`, `Applied`, `Rejected`, `Conflict`, `Failed`; Default: Pending Review |
| Recommendation | Pick List | values (exactly, in this order): `ACTIVE`, `INACTIVE`, `MANUAL_REVIEW` |
| Recommendation Reasons | Multi Line |  |
| Alerts | Multi Line |  |
| Required Documents | Multi Line |  |
| Documents Found | Number |  |
| Lowest Confidence | Decimal | 2 decimal places |
| Job ID | Single Line |  |
| Rules Version | Single Line |  |
| Requested By | Single Line |  |
| Request Reason | Multi Line |  |
| Reviewer | User (lookup) |  |
| Reviewed Time | Date/Time |  |
| Reviewer Notes | Multi Line |  |
| Values Compared | Checkbox | Checkbox: 'I compared every approved value with the source document' |

## 4. Subform "Proposed Changes"

Drag **Subform** into the layout (new section "Proposed Changes"), label **Proposed Changes**, and add:

| Label | Field type | Settings |
|---|---|---|
| Zoho Field | Single Line | API name of the Account field, e.g. Owner_Name. Create the subform with this first column; the setup script adds the rest |
| Field Label | Single Line |  |
| Current Value | Single Line |  |
| Proposed Value | Single Line | Reviewer may edit |
| Action | Pick List | values (exactly, in this order): `FILL`, `CORRECT`, `MATCH`, `HOLD`, `REVIEW_CONFLICT`, `DIFFERS`, `ATTACH`, `NO_PICKLIST_VALUE` |
| Confidence | Decimal | 2 decimal places |
| OCR Check | Pick List | values (exactly, in this order): `EXACT`, `FUZZY`, `CROSS_CHECKED`, `NOT_FOUND`, `CONFLICT`, `UNVERIFIABLE`, `NONE` |
| Evidence | Multi Line (Small) or Single Line | Source document and evidence text |
| Auto Applied | Pick List | values (exactly, in this order): `No`, `Applied`, `Shadow`, `Skipped`, `Failed` |
| Decision | Pick List | values (exactly, in this order): `Pending`, `Approve`, `Edit`, `Reject`; Default: Pending |
| Reviewer Comment | Single Line |  |

## 5. List views (module list page -> Create View)

| View | Criteria |
|---|---|
| Pending Review | Review Status is Pending Review |
| My Reviews | Reviewer is current user AND Review Status is not Applied, Rejected |
| Waiting for Documents | Review Status is Waiting for Documents |
| Applied | Review Status is Applied |
| Failed / Conflict | Review Status is Failed, Conflict |

Columns: DV Number, Account, Review Status, Recommendation, Documents Found, Lowest Confidence,
Reviewer, Created Time. Colour-code Review Status values if your edition offers it.

## 6. Account page

Open an Account in the sandbox: the **Document Verifications** related list appears (from the Account
lookup). Move it near the top of the Account layout.

## 7. Check it

1. Create `.env.sandbox` (copy of `.env`) with `ZOHO_API_DOMAIN=https://sandbox.zohoapis.com`.
2. Make sure the token has `ZohoCRM.settings.modules.READ` (`python scripts/zoho_auth.py scopes`;
   generate a new grant code + `exchange-code --env .env.sandbox` if not).
3. Run (Windows, `.venv` active):

```cmd
python scripts/check_zoho_setup.py --env .env.sandbox
```

Fix every `FAIL` line in Zoho and run again until **All checks passed.** The API names Zoho
generated are saved to `zoho/review_module_api_names.json`.

## Later

Blueprint on Review Status (Start Review -> Approve / Reject / Request Documents; Approve requires a
Decision on every row and **Values Compared** ticked), a "Verification Reviewer" profile, then
Canvas for the record page and a widget for the image-beside-table view.

## Engine -> review records (Phase 3)

With `CREATE_REVIEW_RECORDS=true` the worker creates one **Document Verifications** record per
assessed job: summary fields, alerts, reasons, required documents, one **Proposed Changes** row per
proposal (rows that need a decision first, with `Decision = Pending`; MATCH rows for context;
automatically applied rows marked `Auto Applied = Applied`) and the cleaned page images as
attachments. Review Status: `Pending Review` if anything needs a decision, else
`Waiting for Documents` if documents are missing/expired, else `Applied`.

Scopes needed: `ZohoCRM.modules.custom.CREATE` (+ `.READ`, `.UPDATE` for the review flow) and
`ZohoCRM.modules.attachments.CREATE` (all in `python scripts/zoho_auth.py scopes`).

Try it in the sandbox from an offline assessment:

```cmd
python scripts/create_review_record.py samples/<account_id> --account-id <sandbox Account id>
python scripts/create_review_record.py samples/<account_id> --account-id <sandbox Account id> --apply
```
