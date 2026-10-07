# Automatic Zoho updates for high-confidence values

Setting `AUTO_APPLY_MODE` in `.env`:

| Mode | What happens |
|---|---|
| `off` (default) | Nothing is written; every proposal goes to human review |
| `shadow` | Records in `field_updates` what **would** be written (status SHADOW). Zoho untouched. Run this on real accounts first and check the records |
| `on` | Writes qualifying values to the Zoho Account (status APPLIED). Needs the `ZohoCRM.modules.accounts.UPDATE` scope |

## When a value is written without review (all must hold)

1. Field and action allowed in `AUTO_APPLY_RULES` (`app/assessment/policy.py`):

   | Field | FILL (Zoho empty) | CORRECT (overwrite) |
   |---|---|---|
   | Tax_Identification_Number_TIN | 1 document | 2 agreeing documents |
   | Owner_Name | 1 document | 2 agreeing documents |
   | Invoice_Company_Name | 1 document | 2 agreeing documents |
   | Type_of_Business_Organization | 1 document | never |
   | Business_Style | 1 document | never |

   Never automatic: Account_Name, addresses, file attachments, Customer_Status.
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
