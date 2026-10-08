"""Create a "Document Verifications" review record in the Zoho sandbox from a customer that
was assessed offline (scripts/assess_account.py saves samples/<id>/assessment.json).

    python scripts/create_review_record.py samples/<id> --account-id <sandbox Account id>
    python scripts/create_review_record.py samples/<id> --env .env.sandbox --account-id <id> --apply

Sandbox Account ids differ from production: open a test Account in the sandbox and copy the
number from its URL. The customer's document files are attached to the record.
Refuses non-sandbox domains unless --allow-production.
"""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho.errors import ZohoError
from app.zoho.review_record import build_record, load_names

MAX_ATTACH_BYTES = 20 * 1024 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a review record in the Zoho sandbox")
    parser.add_argument("folder", type=Path, help="samples/<account_id> (after assess_account)")
    parser.add_argument("--env", default=".env.sandbox", type=Path)
    parser.add_argument("--account-id", help="Account id to link to (default: folder name)")
    parser.add_argument("--apply", action="store_true", help="really create (default: dry run)")
    parser.add_argument("--no-attachments", action="store_true")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    source = args.folder / "assessment.json"
    if not source.exists():
        print(f"{source} not found. Run scripts/assess_account.py {args.folder} first.")
        return 1
    saved = json.loads(source.read_text(encoding="utf-8"))
    assessment = saved.get("assessment", saved)
    account_id = args.account_id or args.folder.name
    names = load_names()
    record = build_record(assessment, account_id, job_id=f"offline:{args.folder.name}",
                          requested_by="assess_account.py",
                          request_reason="Test record created from an offline assessment",
                          names=names)

    rows = record.get(names["fields"]["Proposed Changes"], [])
    print(f"Record for Account {account_id} in module {names['module']}:")
    for key, value in record.items():
        if key != names["fields"]["Proposed Changes"]:
            print(f"  {key:24} {str(value)[:110]}")
    print(f"  {len(rows)} Proposed Changes row(s):")
    sf = names["subform_fields"]
    for r in rows:
        print(f"    {r.get(sf['Action'], ''):17} {r.get(sf['Zoho Field'], ''):30} "
              f"{str(r.get(sf['Current Value'], ''))[:25]:25} -> "
              f"{str(r.get(sf['Proposed Value'], ''))[:30]:30} "
              f"decision={r.get(sf['Decision'], '-')} auto={r.get(sf['Auto Applied'])}")
    files = [] if args.no_attachments else sorted(
        p for p in (args.folder / "files").glob("*")
        if p.is_file() and p.suffix.lower() != ".json" and p.stat().st_size <= MAX_ATTACH_BYTES)
    print(f"  attachments: {', '.join(p.name for p in files) or 'none'}")

    if not args.apply:
        print("\nDRY RUN: nothing created. Add --apply to create it.")
        return 0
    settings = build_settings(read_env(args.env))
    if "sandbox" not in settings.zoho_api_domain and not args.allow_production:
        print(f"STOP  {settings.zoho_api_domain} is not a sandbox (use --allow-production).")
        return 2
    client = build_client(settings)
    try:
        review_id = client.create_record(names["module"], record)
    except ZohoError as exc:
        print(f"FAIL  record not created: {exc}")
        if "INVALID_DATA" in str(exc) and "Account" in str(exc):
            print("      Is --account-id an Account that exists in this (sandbox) org?")
        return 1
    print(f"\nOK  created review record {review_id}")
    failed = 0
    for path in files:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            client.upload_attachment(names["module"], review_id, path.name, path.read_bytes(),
                                     mime)
            print(f"    attached {path.name}")
        except ZohoError as exc:
            failed += 1
            print(f"FAIL  attachment {path.name}: {exc}")
    print("Open Document Verifications in the sandbox to see it.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
