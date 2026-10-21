"""Run the request poller: python -m app.tools.poll_requests [--once]

Every POLL_INTERVAL_SECONDS asks Zoho for Accounts marked by the Verify Account button and
starts their verification jobs. Runs as the "poller" service in docker-compose.
"""

import argparse
import logging
import sys
import time

from app.api.deps import get_enqueuer
from app.config import get_settings
from app.db.session import session_scope
from app.logging import setup_logging
from app.services.request_poller import poll_once
from app.zoho.factory import get_zoho_client

log = logging.getLogger("app.poller")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll Zoho for Verify Account requests")
    parser.add_argument("--once", action="store_true", help="poll one time and exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    client, enqueue = get_zoho_client(), get_enqueuer()
    log.info("Polling Zoho every %ss for %s = true", settings.poll_interval_seconds,
             settings.request_flag_field)
    while True:
        try:
            with session_scope() as db:
                started = poll_once(db, client, settings, enqueue)
            if started:
                log.info("Started %d job(s)", len(started))
        except Exception:  # noqa: BLE001 - keep polling after unexpected errors
            log.exception("Polling round failed")
        if args.once:
            return 0
        time.sleep(max(10, settings.poll_interval_seconds))


if __name__ == "__main__":
    sys.exit(main())
