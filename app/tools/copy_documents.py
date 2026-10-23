"""Copy a client's documents to a test Account (e.g. production -> sandbox), for testing.

    python scripts/copy_documents.py --from-account <id> --to-account <id> \
        [--from-env .env] [--to-env .env.sandbox] [--dry-run]

    python scripts/copy_documents.py --from-folder samples/<id> --to-account <id> \
        [--to-env .env.sandbox] [--dry-run]

Finds the documents exactly where the engine looks (Attachments, file-upload fields, Note
attachments) - or takes the files of a folder made by scripts/fetch_account.py - and uploads
each one to the target Account as an Attachment with its original file name. Files already
attached to the target under the same name are skipped, so it is safe to run twice.
Needs, on the target org's token: ZohoCRM.modules.attachments.CREATE (zoho_auth.py scopes).
"""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from app.pipeline.sources import MODULE, discover_sources
from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho.errors import FileTooLargeError, ZohoError


def _from_zoho(env: Path, account_id: str) -> list[tuple[str, bytes]]:
    settings = build_settings(read_env(env))
    client = build_client(settings)
    account = client.get_record(MODULE, account_id)
    print(f"Source: {account.get('Account_Name')} ({account_id}) from {env}")
    files = []
    for ref in discover_sources(client, account_id, account, settings):
        try:
            files.append((ref.file_name or f"{ref.file_ref}.bin", ref.download()))
            print(f"  found  {ref.source.value:<11} {ref.file_name}")
        except (FileTooLargeError, ZohoError) as exc:
            print(f"  SKIP   {ref.source.value:<11} {ref.file_name}: {exc}")
    return files


def _from_folder(folder: Path) -> list[tuple[str, bytes]]:
    manifest = folder / "manifest.json"
    files = []
    if manifest.exists():   # folder made by fetch_account.py: use the original names
        data = json.loads(manifest.read_text(encoding="utf-8"))
        print(f"Source: {data.get('account_name')} ({data.get('account_id')}) from {folder}")
        for e in data.get("files", []):
            if e.get("saved_as") and e.get("status") == "OK":
                files.append((e.get("file_name") or Path(e["saved_as"]).name,
                              (folder / e["saved_as"]).read_bytes()))
    else:                    # any folder of document files
        print(f"Source: folder {folder}")
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.endswith(".json"):
                files.append((p.name, p.read_bytes()))
    for name, _ in files:
        print(f"  found  {name}")
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy a client's documents to a test Account")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-account", help="source Account id (in --from-env's org)")
    src.add_argument("--from-folder", type=Path, help="folder from fetch_account.py, or files")
    ap.add_argument("--from-env", type=Path, default=Path(".env"))
    ap.add_argument("--to-account", required=True, help="target (test) Account id")
    ap.add_argument("--to-env", type=Path, default=Path(".env.sandbox"))
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied")
    a = ap.parse_args(argv)

    files = (_from_zoho(a.from_env, a.from_account) if a.from_account
             else _from_folder(a.from_folder))
    if not files:
        print("No documents found at the source.")
        return 1

    settings = build_settings(read_env(a.to_env))
    target = build_client(settings)
    account = target.get_record(MODULE, a.to_account)
    print(f"Target: {account.get('Account_Name')} ({a.to_account}) in {a.to_env}")
    existing = {str(x.get("File_Name")) for x in target.list_attachments(MODULE, a.to_account)}

    copied = skipped = 0
    for name, data in files:
        if name in existing:
            print(f"  skip   {name} (already attached)")
            skipped += 1
            continue
        if a.dry_run:
            print(f"  would copy {name} ({len(data):,} bytes)")
            continue
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            target.upload_attachment(MODULE, a.to_account, name, data, kind)
            print(f"  copied {name} ({len(data):,} bytes)")
            existing.add(name)
            copied += 1
        except ZohoError as exc:
            print(f"  FAILED {name}: {exc}")
    print(f"Done: {copied} copied, {skipped} already there"
          + (" (dry run, nothing uploaded)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
