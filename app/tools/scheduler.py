"""Background scheduler: python -m app.tools.scheduler

- every REVIEW_APPLY_INTERVAL_SECONDS: apply reviews approved in Zoho to their Accounts
- every POLL_INTERVAL_SECONDS (only if REQUEST_POLLING=true): pull-mode Verify Account requests

Runs as the "scheduler" service in docker-compose. Only the engine calls Zoho; Zoho never
needs to reach the engine for these.
"""

import logging
import sys
import time
from datetime import datetime

from app.api.deps import get_enqueuer
from app.config import get_settings
from app.db.session import session_scope
from app.logging import setup_logging
from app.notify.events import check_expiry, notify_applied_reviews
from app.notify.notifier import MANILA, Notifier
from app.services.request_poller import poll_once
from app.services.review_applier import apply_approved
from app.zoho.factory import get_zoho_client

log = logging.getLogger("app.scheduler")


def run_due(now: float, last: dict, settings, client, enqueue, notifier=None) -> None:
    if settings.apply_approved_reviews and \
            now - last.get("reviews", 0) >= settings.review_apply_interval_seconds:
        last["reviews"] = now
        with session_scope() as db:
            done = apply_approved(db, client, settings)
        if done:
            log.info("Applied %d approved review(s)", len(done))
    if notifier and notifier.enabled and settings.notify_reviews and \
            now - last.get("review_notes", 0) >= settings.review_apply_interval_seconds:
        last["review_notes"] = now
        with session_scope() as db:
            notify_applied_reviews(db, client, notifier)
    manila = datetime.now(MANILA)
    if notifier and notifier.enabled and manila.hour >= settings.expiry_check_hour and \
            last.get("expiry_day") != manila.date():
        last["expiry_day"] = manila.date()
        with session_scope() as db:
            done = check_expiry(db, client, notifier, settings, enqueue, manila.date())
        log.info("Expiry check: %s", done)
    if settings.request_polling and now - last.get("requests", 0) >= \
            settings.poll_interval_seconds:
        last["requests"] = now
        with session_scope() as db:
            poll_once(db, client, settings, enqueue)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    client, enqueue = get_zoho_client(), get_enqueuer()
    notifier = Notifier.from_settings(settings)
    log.info("Scheduler started: approved reviews every %ss (%s), requests every %ss (%s)",
             settings.review_apply_interval_seconds,
             "on" if settings.apply_approved_reviews else "off",
             settings.poll_interval_seconds, "on" if settings.request_polling else "off")
    last: dict = {}
    once = bool(argv) and "--once" in argv
    while True:
        try:
            run_due(time.monotonic() + (10**9 if once else 0), last, settings, client, enqueue,
                    notifier)
        except Exception:  # noqa: BLE001 - keep running after unexpected errors
            log.exception("Scheduler round failed")
        if once:
            return 0
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
