"""Write high-confidence proposals to Zoho (mode "on") or only record them (mode "shadow").

Safety: Zoho is re-read just before writing; a field someone changed after the job started is
skipped. Everything is recorded in field_updates with the old value. Proposals not applied
stay in the assessment for human review.
"""

import logging

from sqlalchemy.orm import Session

from app.assessment.auto_apply import plan
from app.db.models import FieldUpdate, JobAssessment, VerificationJob
from app.services.audit import record_event
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError

log = logging.getLogger(__name__)


def _same(a, b) -> bool:
    norm = lambda v: "" if v is None else " ".join(str(v).split()).upper()  # noqa: E731
    return norm(a) == norm(b)


def auto_apply_job(db: Session, job: VerificationJob, client: ZohoClient | None, mode: str,
                   threshold: float, triggers: list[str] | None = None) -> list[FieldUpdate]:
    if mode not in ("shadow", "on"):
        return []
    row = db.get(JobAssessment, job.id)
    if row is None:
        return []
    items = plan(row.assessment, threshold)
    auto = [i for i in items if i["decision"] == "AUTO"]
    updates: list[FieldUpdate] = []

    def record(item, status, detail=None):
        u = FieldUpdate(job_id=job.id, account_id=job.account_id,
                        zoho_field=item["zoho_field"], action=item["action"],
                        old_value=item["current_value"], new_value=item["new_value"],
                        confidence=item["confidence"], grounding=item["grounding"],
                        sources=item["sources"], mode=mode, status=status, detail=detail)
        db.add(u)
        updates.append(u)

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
                    status, detail = "APPLIED", None
                except ZohoError as exc:
                    status, detail = "FAILED", str(exc)[:1000]
                for item in auto:
                    if item["zoho_field"] in to_write:
                        record(item, status, detail or item["reason"])

    # Tell the review step what happened to each proposal.
    applied = {u.zoho_field: u.status for u in updates}
    assessment = dict(row.assessment)
    assessment["auto_apply"] = {"mode": mode, "threshold": threshold, "plan": items}
    for p in assessment["proposals"]:
        if p["zoho_field"] in applied:
            p["auto_apply_status"] = applied[p["zoho_field"]]
    row.assessment = assessment
    record_event(db, "AUTO_APPLY", f"mode={mode}: " + ", ".join(
        f"{u.zoho_field}={u.status}" for u in updates) or "nothing qualified", job.id,
        {"threshold": threshold, "fields": applied})
    db.commit()
    log.info("Auto-apply %s: %s", mode, applied, extra={"job_id": str(job.id)})
    return updates
