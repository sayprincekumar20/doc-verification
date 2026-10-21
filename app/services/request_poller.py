"""Pull mode: find Accounts whose Verify Account button was clicked and start their jobs.

The button (Deluge) only sets a checkbox on the Account (plus who / when / why). The engine
asks Zoho for marked Accounts, clears the mark first (so a crash never repeats a request), and
creates the job exactly as the HTTP API would. The engine needs no public address.
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config import Settings
from app.schemas import JobCreate
from app.services.jobs import create_or_get_active_job
from app.zoho.client import ZohoClient
from app.zoho.errors import ZohoError

log = logging.getLogger(__name__)


def poll_once(db: Session, client: ZohoClient, settings: Settings,
              enqueue: Callable[[str], None]) -> list[str]:
    flag = settings.request_flag_field
    try:
        requested = client.search_records("Accounts", f"({flag}:equals:true)")
    except ZohoError as exc:
        log.warning("Polling Zoho for verification requests failed: %s", exc)
        return []
    started = []
    for account in requested:
        account_id = str(account.get("id", ""))
        if not account_id.isdigit():
            continue
        try:
            client.update_record("Accounts", account_id, {flag: False}, [])
        except ZohoError as exc:
            log.warning("Could not clear the request mark on %s: %s", account_id, exc)
            continue   # try again next round rather than risk starting it twice
        req = JobCreate(
            account_id=account_id,
            customer_number=account.get("Inventory_Customer_Number"),
            reason=str(account.get(settings.request_reason_field) or "")[:500],
            document_status="YES",
            requested_by={"email": account.get(settings.request_by_field) or None},
            requested_at=account.get(settings.request_at_field) or None,
            source="crm_button",
        )
        job, created = create_or_get_active_job(db, req)
        if created:
            enqueue(str(job.id))
            started.append(str(job.id))
            log.info("Verification requested in Zoho: job %s for Account %s", job.id, account_id)
        else:
            log.info("Account %s already has an active job %s", account_id, job.id)
    return started
