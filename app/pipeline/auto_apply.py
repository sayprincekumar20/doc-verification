"""Automatic actions after assessment (AUTO_APPLY_MODE = shadow | on):

1. Write high-confidence field values to the Zoho Account.
2. Set Customer_Status = Active when the documents are complete, valid and consistent.
   Never sets an account Inactive (business rule, 2026-10-07).
3. Alert the team about missing / expired / conflicting / unreadable documents: a Note on the
   Account and a Task for the Account owner (not repeated for the same issues within N days).

shadow records everything but writes nothing to Zoho. Safety: Zoho is re-read before writing;
fields changed meanwhile are skipped. All actions are recorded (field_updates, account_alerts).
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assessment.alerts import (
    ACTIVE_VALUE,
    alert_signature,
    build_alerts,
    format_note,
    status_decision,
)
from app.assessment.auto_apply import plan
from app.db.models import AccountAlert, FieldUpdate, JobAssessment, VerificationJob
from app.services.audit import record_event
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError

log = logging.getLogger(__name__)


def _same(a, b) -> bool:
    def norm(v):
        return "" if v is None else " ".join(str(v).split()).upper()
    return norm(a) == norm(b)


def _recent_same_alert(db: Session, account_id: str, signature: str, days: int,
                       job_id) -> bool:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(select(AccountAlert).where(
        AccountAlert.account_id == account_id, AccountAlert.signature == signature,
        AccountAlert.task_status.in_(["CREATED", "SHADOW"]), AccountAlert.job_id != job_id))
    return any(r.created_at.replace(tzinfo=r.created_at.tzinfo or UTC) >= since for r in rows)


def auto_apply_job(db: Session, job: VerificationJob, client: ZohoClient | None, mode: str,
                   threshold: float, triggers: list[str] | None = None, *,
                   alert_tasks: bool = True, task_due_days: int = 3,
                   repeat_after_days: int = 7) -> list[FieldUpdate]:
    if mode not in ("shadow", "on"):
        return []
    row = db.get(JobAssessment, job.id)
    if row is None:
        return []
    assessment = dict(row.assessment)
    today = date.fromisoformat(assessment.get("assessed_on") or date.today().isoformat())
    snapshot = job.account_snapshot or {}

    items = plan(assessment, threshold)
    status = status_decision(assessment, snapshot.get("Customer_Status"), threshold)
    if status[0] == "ACTIVATE":
        items.append({"zoho_field": "Customer_Status", "action": "ACTIVATE",
                      "current_value": snapshot.get("Customer_Status"),
                      "new_value": ACTIVE_VALUE, "confidence": None, "grounding": None,
                      "agreeing_documents": None, "sources": [], "decision": "AUTO",
                      "reason": status[1]})
    auto = [i for i in items if i["decision"] == "AUTO"]
    updates: list[FieldUpdate] = []

    def record(item, st, detail=None):
        u = FieldUpdate(job_id=job.id, account_id=job.account_id,
                        zoho_field=item["zoho_field"], action=item["action"],
                        old_value=item["current_value"], new_value=item["new_value"],
                        confidence=item["confidence"], grounding=item["grounding"],
                        sources=item["sources"], mode=mode, status=st, detail=detail)
        db.add(u)
        updates.append(u)

    # ---- 1 + 2: field values and activation, in one Zoho update ----
    if mode == "shadow" or not auto:
        for item in auto:
            record(item, "SHADOW", item["reason"])
    else:
        try:
            fresh = client.get_record("Accounts", job.account_id)
        except ZohoError as exc:
            fresh = None
            for item in auto:
                record(item, "FAILED", f"Could not re-read the Account: {exc}")
        if fresh is not None:
            to_write = {}
            for item in auto:
                if _same(fresh.get(item["zoho_field"]), item["current_value"]):
                    to_write[item["zoho_field"]] = item["new_value"]
                else:
                    record(item, "SKIPPED_CHANGED",
                           f"Changed in Zoho since the job started "
                           f"(now {fresh.get(item['zoho_field'])!r}); left for review")
            if to_write:
                try:
                    client.update_record("Accounts", job.account_id, to_write, triggers)
                    st, detail = "APPLIED", None
                except ZohoError as exc:
                    st, detail = "FAILED", str(exc)[:1000]
                for item in auto:
                    if item["zoho_field"] in to_write:
                        record(item, st, detail or item["reason"])
    applied = {u.zoho_field: u.status for u in updates}

    # ---- 3: alerts ----
    alerts = build_alerts(assessment, today)
    if status[0] == "NO_CHANGE" and assessment["recommendation"] != "ACTIVE" and _same(
            snapshot.get("Customer_Status"), ACTIVE_VALUE):
        alerts.insert(0, {"severity": "CRITICAL", "code": "ACTIVE_WITH_PROBLEMS",
                          "document_type": None, "message": status[1],
                          "action": "Decide whether this account should stay Active"})
    actionable = [a for a in alerts if a["severity"] in ("CRITICAL", "WARNING")]
    signature = alert_signature(alerts)
    note_status = task_status = "SKIPPED"
    note_id = task_id = None
    detail = None
    if alerts or applied:
        title, content = format_note(assessment, alerts, status, applied)
        repeat = bool(actionable) and _recent_same_alert(db, job.account_id, signature,
                                                         repeat_after_days, job.id)
        want_task = alert_tasks and bool(actionable) and not repeat
        if mode == "shadow":
            note_status, task_status = "SHADOW", "SHADOW" if want_task else "SKIPPED"
        else:
            try:
                note_id = client.create_note("Accounts", job.account_id, title, content)
                note_status = "CREATED"
            except ZohoError as exc:
                note_status, detail = "FAILED", f"note: {exc}"[:500]
            if want_task:
                owner = snapshot.get("Owner") if isinstance(snapshot.get("Owner"), dict) else {}
                first = actionable[0]
                subject = (f"Documents: {first['action']}" if len(actionable) == 1 else
                           f"Documents: {len(actionable)} issues to resolve")
                try:
                    task_id = client.create_task(
                        subject, content, (today + timedelta(days=task_due_days)).isoformat(),
                        job.account_id, (owner or {}).get("id"))
                    task_status = "CREATED"
                except ZohoError as exc:
                    task_status = "FAILED"
                    detail = ((detail or "") + f" task: {exc}")[:1000]
        if repeat:
            detail = ((detail or "") + f" Task not repeated: same issues raised within "
                      f"{repeat_after_days} days.").strip()
        db.add(AccountAlert(job_id=job.id, account_id=job.account_id, mode=mode,
                            signature=signature, alerts=alerts, status_decision=status[0],
                            note_status=note_status, task_status=task_status,
                            zoho_note_id=note_id, zoho_task_id=task_id, detail=detail))

    # ---- record outcome on the assessment for the review step ----
    assessment["auto_apply"] = {"mode": mode, "threshold": threshold, "plan": items,
                                "status_decision": {"decision": status[0], "reason": status[1]},
                                "alerts": alerts, "note": note_status, "task": task_status}
    for p in assessment["proposals"]:
        if p["zoho_field"] in applied:
            p["auto_apply_status"] = applied[p["zoho_field"]]
    row.assessment = assessment
    record_event(db, "AUTO_APPLY", f"mode={mode}; status {status[0]}; " + (", ".join(
        f"{f}={s}" for f, s in applied.items()) or "no field updates") +
        f"; {len(alerts)} alert(s), note {note_status}, task {task_status}", job.id,
        {"fields": applied, "status": status[0], "alerts": [a["code"] for a in alerts]})
    db.commit()
    log.info("Automatic actions %s: %s", mode, applied, extra={"job_id": str(job.id)})
    return updates
