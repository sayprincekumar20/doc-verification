"""What gets announced in Zoho Cliq and logged in the Google Sheet.

1. Verification done   -> Cliq + 'Verifications' (+ 'Field Updates' for automatic updates,
                          'Activations' for automatic activation, renewals of alerted documents)
2. Review approved     -> Cliq + 'Field Updates' (+ 'Activations' when the reviewer activated)
3. Document expiring   -> Cliq + 'Document Expiry', once per document, expiry date and level;
                          if the Account has new attachments since its last verification, it is
                          re-verified first and the alert is only sent if still needed.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.assessment.alerts import label
from app.db.models import FieldUpdate, JobAssessment, Notification, VerificationJob
from app.notify.notifier import MANILA, Notifier, already_sent, manila_now, mark_sent
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError
from app.zoho.review_record import FIELD_LABELS, SHORT_LABELS, _date, load_names, short_name

log = logging.getLogger(__name__)

_CHECK_SHORT = {"payment_terms": "COD", "accounting_contact": "Contact", "tin": "TIN",
                "invoice_name": "Invoice name", "billing_address": "Address"}


def _doc(doc_type: str) -> str:
    return SHORT_LABELS.get(doc_type, label(doc_type))


def _field(api: str) -> str:
    return FIELD_LABELS.get(api, api.replace("_", " "))


def _dv_name(client: ZohoClient | None, review_id: str | None) -> str:
    if not (client and review_id):
        return ""
    try:
        return str(client.get_record(load_names()["module"], review_id).get("Name") or "")
    except ZohoError:
        return ""


def _parse(ts) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts)) if ts else None
    except ValueError:
        return None


# ------------------------------------------------------------------ 1. verification done
def notify_verification(db: Session, job: VerificationJob, notifier: Notifier,
                        client: ZohoClient | None = None) -> None:
    row = db.get(JobAssessment, job.id)
    if row is None or not notifier.enabled or already_sent(db, "verification", str(job.id)):
        return
    a, snap = row.assessment, job.account_snapshot or {}
    name = snap.get("Account_Name") or job.account_id
    number = snap.get("Inventory_Customer_Number") or job.customer_number or ""
    dv = _dv_name(client, row.zoho_review_id)
    checks = a.get("activation_checks") or []
    met = sum(1 for c in checks if c["status"] == "PASS")
    missing = [f"{_doc(r['document_type'])} {r['status'].lower()}"
               for r in a.get("requirements", []) if r["status"] in ("MISSING", "EXPIRED")]
    missing += [_CHECK_SHORT.get(c["key"], c["label"]) for c in checks if c["status"] == "FAIL"]
    updates = db.query(FieldUpdate).filter_by(job_id=job.id).all()
    fields = [u for u in updates if u.zoho_field != "Customer_Status"]
    activated = any(u.zoho_field == "Customer_Status" and u.status == "APPLIED" for u in updates)
    applied = [_field(u.zoho_field) for u in fields if u.status == "APPLIED"]
    shadow = [_field(u.zoho_field) for u in fields if u.status == "SHADOW"]
    rec = a["recommendation"].replace("_", " ")
    who = job.requested_by_email or "someone"

    lines = [f"✅ Verification done: {name}" + (f" ({number})" if number else ""),
             f"Requested by {who} · {rec}"
             + (f" · activation checks {met}/{len(checks)}" if checks else "")
             + (f" · {dv}" if dv else "")]
    if missing:
        lines.append("Missing / not met: " + ", ".join(missing))
    if applied:
        lines.append("Updated automatically: " + ", ".join(applied))
    if shadow:
        lines.append("Would update automatically (shadow mode): " + ", ".join(shadow))
    if activated:
        lines.append("Customer Status → Active (automatic)")
    notifier.message("\n".join(lines))

    now = manila_now()
    notifier.rows("Verifications", [[
        now, dv, name, job.account_id, number, who, job.reason or "", rec,
        f"{met}/{len(checks)}" if checks else "", ", ".join(missing),
        len(a.get("documents", [])), "Active (automatic)" if activated else "not changed"]])
    notifier.rows("Field Updates", [[
        now, dv, name, job.account_id, _field(u.zoho_field), u.old_value or "", u.new_value or "",
        u.confidence if u.confidence is not None else "",
        "Shadow - would update" if u.status == "SHADOW" else "Automatic (confidence ≥ 0.95)",
        u.status.replace("_", " ").title()] for u in fields])
    if activated:
        notifier.rows("Activations", [[now, name, job.account_id, number, "Automatic", dv]])
    _announce_renewals(db, job, a, notifier, name, number)
    mark_sent(db, "verification", str(job.id), job.account_id,
              {"recommendation": a["recommendation"], "dv": dv})


def _announce_renewals(db: Session, job: VerificationJob, a: dict, notifier: Notifier,
                       name: str, number: str) -> None:
    """A document alerted earlier now has a later expiry date -> 'renewed'."""
    for r in a.get("requirements", []):
        until = r.get("valid_until")
        if r["status"] != "VALID" or not until:
            continue
        prefix = f"{job.account_id}|{r['document_type']}|"
        alerted = [n for n in db.query(Notification).filter(
            Notification.kind == "expiry", Notification.ref.startswith(prefix)).all()
            if n.ref.split("|")[2] < until]
        ref = f"{job.account_id}|{r['document_type']}|{until}"
        if not alerted or already_sent(db, "renewed", ref):
            continue
        notifier.message(f"♻️ {_doc(r['document_type'])} of {name} renewed: valid until "
                         f"{_date(until)}. No more reminders for the old one.")
        notifier.rows("Document Expiry", [[
            manila_now(), name, job.account_id, number, _doc(r["document_type"]),
            short_name(r.get("file")), until, (date.fromisoformat(until) - date.today()).days,
            "", "Renewed"]])
        mark_sent(db, "renewed", ref, job.account_id)


# ------------------------------------------------------------------ 2. review approved
def notify_applied_reviews(db: Session, client: ZohoClient, notifier: Notifier,
                           now: datetime | None = None, max_age_days: int = 3) -> int:
    """Reviews that reached Applied / Conflict / Failed. Older ones (before this feature or
    more than max_age_days ago) are marked without a message, so nothing floods the channel."""
    if not notifier.enabled:
        return 0
    names = load_names()
    module, f, sf = names["module"], names["fields"], names["subform_fields"]
    now = now or datetime.now(MANILA)
    sent = 0
    for status in ("Applied", "Conflict", "Failed"):
        try:
            found = client.search_records(module, f"({f['Review Status']}:equals:{status})")
        except ZohoError as exc:
            log.warning("Reading %s reviews failed: %s", status, exc)
            continue
        for brief in found:
            rid = str(brief.get("id"))
            if already_sent(db, "review", rid):
                continue
            modified = _parse(brief.get("Modified_Time"))
            if modified and modified < now - timedelta(days=max_age_days):
                mark_sent(db, "review", rid, None, {"silent": True})
                continue
            try:
                rec = client.get_record(module, rid)
            except ZohoError as exc:
                log.warning("Reading review %s failed: %s", rid, exc)
                continue
            _announce_review(db, client, notifier, rec, status, f, sf)
            mark_sent(db, "review", rid, str((rec.get(f["Account"]) or {}).get("id") or ""),
                      {"status": status})
            sent += 1
    return sent


def _announce_review(db, client, notifier, rec, status, f, sf) -> None:
    dv = str(rec.get("Name") or rec.get("id"))
    acc = rec.get(f["Account"]) or {}
    name, acc_id = acc.get("name") or "", str(acc.get("id") or "")
    reviewer = (rec.get(f["Reviewer"]) or {}).get("name") or "the reviewer"
    notes = str(rec.get(f["Reviewer Notes"]) or "")
    rows = rec.get(f["Proposed Changes"]) or []
    decided = [r for r in rows if r.get(sf["Decision"]) in ("Approve", "Edit")]
    applied = [r for r in decided if r.get(sf["Auto Applied"]) == "Applied"]
    skipped = [r for r in decided if r.get(sf["Auto Applied"]) in ("Skipped", "Failed")]
    activated = "Customer Status set to Active" in notes

    labels = [str(r.get(sf["Field Label"]) or r.get(sf["Zoho Field"])) for r in applied]
    if status == "Failed":
        text = f"❌ {dv} for {name}: the write-back failed. See Reviewer Notes in Zoho."
    else:
        text = (f"👍 {dv} approved by {reviewer} for {name}: {len(applied)} field(s) updated"
                + (f" ({', '.join(labels)})" if labels else ""))
        if skipped:
            text += f"; {len(skipped)} not written (the Account had changed: Conflict)"
    if activated:
        text += "\nCustomer Status → Active (reviewer decision)"
    notifier.message(text)

    now = manila_now()
    notifier.rows("Field Updates", [[
        now, dv, name, acc_id, str(r.get(sf["Field Label"]) or r.get(sf["Zoho Field"])),
        r.get(sf["Current Value"]) or "", r.get(sf["Proposed Value"]) or "",
        r.get(sf["Confidence"]) or "", f"Approved by {reviewer}",
        {"Applied": "Applied", "Skipped": "Conflict"}.get(r.get(sf["Auto Applied"]), "Failed")]
        for r in applied + skipped])
    if activated:
        number = ""
        try:
            number = client.get_record("Accounts", acc_id).get("Inventory_Customer_Number") or ""
        except ZohoError:
            pass
        notifier.rows("Activations", [[now, name, acc_id, number, reviewer, dv]])


# ------------------------------------------------------------------ 3. expiry reminders
def _levels(settings) -> list[int]:
    return sorted({int(x) for x in str(settings.expiry_alert_days).split(",") if x.strip()})


def _level(days_left: int, levels: list[int]) -> str | None:
    if days_left < 0:
        return "expired"
    reached = [lv for lv in levels if days_left <= lv]
    if not reached:
        return None
    lv = min(reached)
    return "today" if lv == 0 else f"{lv} days"


def _latest_assessments(db: Session) -> list[tuple[JobAssessment, VerificationJob]]:
    latest = (db.query(VerificationJob.account_id.label("acc"),
                       func.max(JobAssessment.created_at).label("at"))
              .join(JobAssessment, JobAssessment.job_id == VerificationJob.id)
              .group_by(VerificationJob.account_id).subquery())
    return (db.query(JobAssessment, VerificationJob)
            .join(VerificationJob, JobAssessment.job_id == VerificationJob.id)
            .join(latest, (VerificationJob.account_id == latest.c.acc)
                  & (JobAssessment.created_at == latest.c.at)).all())


def _new_attachments(client: ZohoClient, account_id: str, since: datetime) -> bool:
    try:
        files = client.list_attachments("Accounts", account_id)
    except ZohoError as exc:
        log.warning("Attachments of %s could not be checked: %s", account_id, exc)
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=MANILA)
    return any((_parse(x.get("Created_Time")) or since) > since for x in files)


def check_expiry(db: Session, client: ZohoClient, notifier: Notifier, settings,
                 enqueue: Callable[[str], None], today: date) -> dict:
    """Daily: remind about documents expiring in 30 / 7 / 0 days or expired, once each."""
    from app.schemas import JobCreate
    from app.services.jobs import create_or_get_active_job

    summary = {"alerts": 0, "rechecks": 0}
    levels = _levels(settings)
    for row, job in _latest_assessments(db):
        snap = job.account_snapshot or {}
        name = snap.get("Account_Name") or job.account_id
        number = snap.get("Inventory_Customer_Number") or job.customer_number or ""
        due = []
        for r in row.assessment.get("requirements", []):
            until = r.get("valid_until")
            if r["status"] not in ("VALID", "EXPIRED") or not until:
                continue
            days = (date.fromisoformat(until) - today).days
            level = _level(days, levels)
            ref = f"{job.account_id}|{r['document_type']}|{until}|{level}"
            if level and not already_sent(db, "expiry", ref):
                due.append((r, until, days, level, ref))
        if not due:
            continue
        # New files uploaded since the last verification? Re-verify first; the alert is only
        # sent later if the new verification still shows the document expiring.
        if _new_attachments(client, job.account_id, row.created_at):
            new_job, created = create_or_get_active_job(db, JobCreate(
                account_id=job.account_id, customer_number=number or None,
                reason="New documents found before an expiry reminder", document_status="YES",
                source="schedule"))
            if created:
                enqueue(str(new_job.id))
            summary["rechecks"] += 1
            continue
        for r, until, days, level, ref in due:
            doc = _doc(r["document_type"])
            when = {"expired": f"expired on {_date(until)} ({-days} days ago)",
                    "today": f"expires today ({_date(until)})"}.get(
                level, f"expires on {_date(until)} (in {days} days)")
            notifier.message(f"⚠️ {doc} of {name}" + (f" ({number})" if number else "")
                             + f" {when}. Please collect the renewed {doc} and attach it "
                               f"to the Account.")
            notifier.rows("Document Expiry", [[
                manila_now(), name, job.account_id, number, doc, short_name(r.get("file")),
                until, days, level, "Open"]])
            mark_sent(db, "expiry", ref, job.account_id, {"days_left": days})
            summary["alerts"] += 1
    db.commit()
    return summary


def job_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
