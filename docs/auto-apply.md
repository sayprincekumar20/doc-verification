# Automatic actions: field updates, activation, alerts

Permission from the business (2026-10-07): the system may **activate** accounts and **update
customer fields** automatically, and must **alert** the team about missing or expired documents.
It never sets an account Inactive.

Setting `AUTO_APPLY_MODE` in `.env`:

| Mode | What happens |
|---|---|
| `off` (default) | Nothing is written; every proposal goes to human review |
| `shadow` | Records in `field_updates` what **would** be written (status SHADOW). Zoho untouched. Run this on real accounts first and check the records |
| `on` | Writes qualifying values to the Zoho Account (status APPLIED). Needs the `ZohoCRM.modules.accounts.UPDATE` scope |

## Customer_Status

| Situation | Automatic action |
|---|---|
| Recommendation ACTIVE (required documents present, valid, consistent), owner and TIN confirmed with confidence >= threshold, no unresolved conflicts | `Customer_Status = Active` (recorded as ACTIVATE) |
| Already Active and documents fine | nothing (ALREADY_ACTIVE) |
| INACTIVE or MANUAL_REVIEW | status unchanged; alerts sent. If the account is Active in Zoho, an extra CRITICAL alert asks a person to decide |

## Alerts

A **Note** on the Account lists all issues; a **Task** (High priority, due in
`ALERT_TASK_DUE_DAYS`, assigned to the Account owner) is created when action is needed. The
same set of issues does not create another task within `ALERT_REPEAT_AFTER_DAYS` (7).

| Alert | Severity |
|---|---|
| Required document expired (with date and days ago) | CRITICAL |
| Documents of different people / TIN conflict / BIR branch mismatch | CRITICAL |
| Account Active in Zoho but documents not OK | CRITICAL |
| Required document missing | WARNING |
| Document expiring within 30 days | WARNING |
| File could not be read | WARNING |
| Fields needing a person's check | INFO (note only) |

Scopes needed for `on`: `ZohoCRM.modules.accounts.UPDATE`, `ZohoCRM.modules.notes.CREATE`,
`ZohoCRM.modules.tasks.CREATE` (`python scripts/zoho_auth.py scopes`). Test Note/Task creation
on a Zoho sandbox or a test Account first.

## When a value is written without review (all must hold)

1. Field and action allowed in `AUTO_APPLY_RULES` (`app/assessment/policy.py`):

   | Field | FILL (Zoho empty) | CORRECT (overwrite) |
   |---|---|---|
   | Tax_Identification_Number_TIN | 1 document | 2 agreeing documents |
   | Owner_Name | 1 document | 2 agreeing documents |
   | Invoice_Company_Name | 1 document | 2 agreeing documents |
   | Type_of_Business_Organization | 1 document | never |
   | Business_Style | 1 document | never |

   Never automatic: Account_Name, addresses, file attachments. Customer_Status: only Active
   (see above), never Inactive.
2. Confidence >= `AUTO_APPLY_THRESHOLD` (default 0.95).
3. The value was confirmed: grounding EXACT (OCR read the same value) or CROSS_CHECKED.
4. Not on HOLD / not needing attention, and the account has no critical problem (documents of
   different people, TIN conflict, BIR branch mismatch...). An expired document alone does not
   block: a TIN from a valid BIR 2303 is still correct.

Why 0.95: in the 2026-10-07 benchmark (101 fields, 10 real documents), every wrong value had
confidence 0.5 or lower; OCR-confirmed correct values scored 0.95-0.98. The sample is small:
start with `shadow`, compare its records with what reviewers approve, then switch to `on`.

## Safety

- Zoho is re-read just before writing; a field changed by someone after the job started is
  skipped (SKIPPED_CHANGED) and left for review.
- Our update starts no Zoho workflows (`trigger: []`), so it can't start another verification.
- Every write is logged in `field_updates` with old value, new value, confidence, grounding and
  source documents (`GET /v1/jobs/{id}/updates`), so any change can be traced and undone.
- Everything not applied stays in the assessment for human review.

Preview without writing: `scripts/assess_account.py samples/<account_id>` prints the plan.
