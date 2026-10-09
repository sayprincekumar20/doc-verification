"""Apply approved "Document Verifications" reviews to their Accounts.

    python scripts/apply_reviews.py --env .env.sandbox            # dry run: what would be written
    python scripts/apply_reviews.py --env .env.sandbox --apply    # write
    python scripts/apply_reviews.py --env .env.sandbox --review-id <id> --apply

Finds records with Review Status = Approved (or the one given), writes rows with Decision
Approve/Edit to the Account (after checking the Account didn't change meanwhile), then sets the
record to Applied / Conflict / Failed with a per-row result. Refuses non-sandbox domains unless
--allow-production.
"""

import argparse
import sys
from pathlib import Path

from app.review.apply import apply_review, approved_reviews
from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho.errors import ZohoError
from app.zoho.review_record import load_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply approved reviews to Accounts")
    parser.add_argument("--env", default=".env.sandbox", type=Path)
    parser.add_argument("--review-id", help="only this Document Verification record")
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    settings = build_settings(read_env(args.env))
    if "sandbox" not in settings.zoho_api_domain and not args.allow_production:
        print(f"STOP  {settings.zoho_api_domain} is not a sandbox (use --allow-production).")
        return 2
    client = build_client(settings)
    names = load_names()
    try:
        reviews = ([client.get_record(names["module"], args.review_id)] if args.review_id
                   else approved_reviews(client, names))
    except ZohoError as exc:
        print(f"FAIL  {exc}")
        return 1
    mode = "APPLY" if args.apply else "DRY RUN (nothing written; add --apply)"
    print(f"{len(reviews)} approved review(s); mode: {mode}")
    failed = 0
    for review in reviews:
        status = review.get(names["fields"]["Review Status"])
        if status != "Approved":
            print(f"\n{review.get('Name')} ({review.get('id')}): status {status!r}, "
                  "not Approved - skipped")
            continue
        r = apply_review(client, review, dry_run=not args.apply, names=names, triggers=[])
        account = (review.get(names["fields"]["Account"]) or {}).get("name") or r.account_id
        print(f"\n{review.get('Name')} ({r.review_id}) -> Account {account}")
        for row in r.rows:
            value = f" -> {row.value}" if row.outcome in ("WRITE", "APPLIED") else ""
            print(f"  {row.outcome:12} {row.zoho_field:30} [{row.decision or '-'}]{value}"
                  + (f"  ({row.detail})" if row.detail and row.outcome != "WRITE" else ""))
        if r.error:
            failed += 1
            print(f"  FAIL  {r.error}")
        if r.status:
            print(f"  Review Status -> {r.status}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
