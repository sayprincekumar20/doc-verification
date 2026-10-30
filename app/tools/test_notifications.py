"""Send one test Cliq message and one test Sheet row: python scripts/test_notifications.py"""

import argparse
import sys
from pathlib import Path

from app.notify.notifier import Notifier, manila_now
from app.tools.envfile import read_env
from app.tools.zoho_auth import build_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Test the Cliq channel and the Google Sheet log")
    ap.add_argument("--env", type=Path, default=Path(".env"))
    a = ap.parse_args(argv)
    n = Notifier.from_settings(build_settings(read_env(a.env)))
    print("Cliq:", "configured" if n.cliq else "NOT configured (CLIQ_MESSAGE_URL / TOKEN)")
    print("Google Sheet:", "configured" if n.sheets else
          "NOT configured (GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_FILE)")
    if n.cliq:
        ok = n.cliq.post(f"🔧 Test from the document verification engine ({manila_now()})")
        print("Cliq message:", "sent" if ok else "FAILED (see the warning above)")
    if n.sheets:
        n.sheets.ensure_tabs(__import__("app.notify.notifier", fromlist=["TABS"]).TABS)
        ok = n.sheets.append("Verifications", [[manila_now(), "TEST", "Test row", "", "", "",
                                                 "connection test"]])
        print("Sheet row:", "written (tab Verifications)" if ok else "FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
