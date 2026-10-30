"""Phase 3 write-back: apply reviewer-approved rows of a "Document Verifications" record to
its Account.

Rules
- Only rows with Decision = Approve or Edit are written ("Edit" = the reviewer changed the
  Proposed Value). Pending / Reject rows are never written.
- Only Account fields in WRITABLE_FIELDS; file rows (ATTACH) are not automated yet.
- Safety: the Account is re-read; a row whose field changed since the review was created
  (Account value != row's Current Value) is skipped (Conflict) and left for a person.
- Result per row in "Auto Applied" (Applied / Skipped / Failed); record status becomes
  Applied, Conflict or Failed; Reviewed Time set if empty.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.assessment.alerts import ACTIVE_VALUE
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError
from app.zoho.review_record import load_names

WRITABLE_FIELDS = {"Owner_Name", "Tax_Identification_Number_TIN",
                   "Type_of_Business_Organization", "Business_Style", "Invoice_Company_Name",
                   "Billing_Street", "Account_Name", "Email", "Phone"}
APPROVED = {"Approve", "Edit"}


@dataclass
class RowResult:
    row_id: str
    zoho_field: str
    decision: str | None
    value: Any = None
    outcome: str = "NOT_APPROVED"   # WRITE | APPLIED | CONFLICT | UNSUPPORTED | NO_VALUE | FAILED
    detail: str = ""


@dataclass
class ApplyResult:
    review_id: str
    account_id: str | None
    rows: list[RowResult] = field(default_factory=list)
    status: str | None = None      # Applied | Conflict | Failed (None in dry run)
    error: str | None = None
    activated: bool | None = None  # reviewer ticked Activate Account: True set / False failed
    notes: list[str] = field(default_factory=list)


def _norm(v) -> str:
    return "" if v in (None, "") else " ".join(str(v).split()).upper()


def apply_rows(client: ZohoClient, account_id: str | None, rows: list[dict], *,
               dry_run: bool = True, triggers: list[str] | None = None,
               activate: bool = False, review_id: str = "") -> ApplyResult:
    """Write approved rows to the Account, safely. Each row: id, zoho_field, decision,
    value (proposed or edited), current_value (as seen when the review was made).
    Only Approve / Edit rows of writable fields; a field changed in Zoho since the review is a
    CONFLICT. activate=True (reviewer's decision) then sets Customer Status = Active, never
    after a failed write. Used by the Zoho review module and the dashboard review API."""
    result = ApplyResult(review_id, account_id)
    for row in rows:
        r = RowResult(str(row.get("id")), row.get("zoho_field") or "", row.get("decision"),
                      row.get("value"))
        if r.decision not in APPROVED:
            r.detail = f"Decision {r.decision or 'empty'}: not written"
        elif r.zoho_field not in WRITABLE_FIELDS:
            r.outcome, r.detail = "UNSUPPORTED", f"{r.zoho_field} is not written automatically"
        elif r.value in (None, ""):
            r.outcome, r.detail = "NO_VALUE", "Approved but Proposed Value is empty"
        else:
            r.outcome = "WRITE"
        result.rows.append(r)

    if not account_id:
        result.error = "Review has no Account"
        return result
    try:
        current = client.get_record("Accounts", account_id)
    except ZohoError as exc:
        result.error = f"Could not read the Account: {exc}"
        return result
    to_write = {}
    for r, row in zip(result.rows, rows, strict=True):
        if r.outcome != "WRITE":
            continue
        if _norm(current.get(r.zoho_field)) != _norm(row.get("current_value")):
            r.outcome = "CONFLICT"
            r.detail = (f"Account now has {current.get(r.zoho_field)!r}, review expected "
                        f"{row.get('current_value')!r}")
        else:
            to_write[r.zoho_field] = r.value
    if dry_run:
        return result

    if to_write:
        try:
            client.update_record("Accounts", account_id, to_write, triggers)
            for r in result.rows:
                if r.outcome == "WRITE":
                    r.outcome, r.detail = "APPLIED", "Written to the Account"
        except ZohoError as exc:
            for r in result.rows:
                if r.outcome == "WRITE":
                    r.outcome, r.detail = "FAILED", str(exc)[:300]
            result.error = str(exc)[:500]
    outcomes = {r.outcome for r in result.rows}
    result.status = ("Failed" if "FAILED" in outcomes else
                     "Conflict" if "CONFLICT" in outcomes else "Applied")
    result.notes = [f"{r.zoho_field}: {r.outcome} - {r.detail}" for r in result.rows
                    if r.outcome not in ("NOT_APPROVED",)]
    if activate:
        if result.status == "Failed":
            result.activated = False
            result.notes.append("Account NOT activated: the field update failed")
        else:
            try:
                client.update_record("Accounts", account_id,
                                     {"Customer_Status": ACTIVE_VALUE}, triggers)
                result.activated = True
                result.notes.append("Customer Status set to Active (reviewer decision)")
            except ZohoError as exc:
                result.activated = False
                result.notes.append(f"Activation failed: {str(exc)[:300]}")
    return result


def apply_review(client: ZohoClient, review: dict, *, dry_run: bool = True,
                 names: dict | None = None, triggers: list[str] | None = None) -> ApplyResult:
    """Zoho 'Document Verifications' record: apply its approved rows, then write the outcome
    back to the record (status, per-row Auto Applied, Reviewer Notes)."""
    names = names or load_names()
    f, sf, module = names["fields"], names["subform_fields"], names["module"]
    account = review.get(f["Account"]) or {}
    rows = review.get(f["Proposed Changes"]) or []
    result = apply_rows(
        client, account.get("id"),
        [{"id": row.get("id"), "zoho_field": row.get(sf["Zoho Field"]),
          "decision": row.get(sf["Decision"]), "value": row.get(sf["Proposed Value"]),
          "current_value": row.get(sf["Current Value"])} for row in rows],
        dry_run=dry_run, triggers=triggers, review_id=str(review.get("id")),
        activate=bool(review.get(f.get("Activate Account", "Activate_Account"))))
    if result.error and result.error.startswith("Review has no Account"):
        result.error = "Review record has no Account"
    if dry_run or result.status is None:
        return result

    # Write back the per-row outcome. All rows are sent with their ids: Zoho replaces the
    # subform with the rows in the request, so leaving one out would delete it.
    auto = {"APPLIED": "Applied", "CONFLICT": "Skipped", "FAILED": "Failed",
            "UNSUPPORTED": "Skipped", "NO_VALUE": "Skipped"}
    update = {f["Review Status"]: result.status,
              f["Proposed Changes"]: [
                  {"id": r.row_id, sf["Auto Applied"]: auto.get(r.outcome, "No")}
                  for r in result.rows]}
    if not review.get(f.get("Reviewed Time", "Reviewed_Time")):
        update[f.get("Reviewed Time", "Reviewed_Time")] = \
            datetime.now(UTC).replace(microsecond=0).isoformat()
    if result.notes:
        update[f.get("Reviewer Notes", "Reviewer_Notes")] = "\n".join(
            filter(None, [review.get(f.get("Reviewer Notes", "Reviewer_Notes")),
                          "Write-back:", *result.notes]))[:2000]
    try:
        client.update_record(module, result.review_id, update, triggers)
    except ZohoError as exc:
        result.error = ((result.error or "") + f" Review record not updated: {exc}").strip()
    return result


def approved_reviews(client: ZohoClient, names: dict | None = None) -> list[dict]:
    names = names or load_names()
    status = names["fields"]["Review Status"]
    found = client.search_records(names["module"], f"({status}:equals:Approved)")
    # search results may omit subforms: fetch each full record
    return [client.get_record(names["module"], str(r["id"])) for r in found]
