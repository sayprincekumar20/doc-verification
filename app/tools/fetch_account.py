"""Download one Account's Zoho data and documents into a local folder.

    python scripts/fetch_account.py <account_id> [--out samples]

Creates samples/<account_id>/ with:
    account.json       full Account record (GET /Accounts/{id})
    notes.json         Notes with $attachments
    attachments.json   Attachments list
    files/             every document file
    manifest.json      one entry per file: source, name, type, size, sha256, status
The samples/ folder is git-ignored: it holds real customer data.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.pipeline.collect import MODULE, discover_sources
from app.pipeline.file_checks import check_file
from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho.errors import FileTooLargeError, ZohoError


def _safe(name: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "file")[:120]


def fetch(account_id: str, out_dir: Path, env_path: Path,
          http: httpx.Client | None = None) -> dict:
    settings = build_settings(read_env(env_path))
    client = build_client(settings, http)
    target = out_dir / account_id
    files_dir = target / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    account = client.get_record(MODULE, account_id)
    notes = client.list_notes(MODULE, account_id)
    attachments = client.list_attachments(MODULE, account_id)
    for name, data in (("account", account), ("notes", notes), ("attachments", attachments)):
        (target / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    entries = []
    for ref in discover_sources(client, account_id, account, settings,
                                attachments=attachments, notes=notes):
        entry = {"source": ref.source.value, "source_field": ref.source_field,
                 "zoho_file_ref": ref.file_ref, "file_name": ref.file_name,
                 "uploaded_by": ref.uploaded_by, "created_time": ref.created_time}
        try:
            data = ref.download()
        except (FileTooLargeError, ZohoError) as exc:
            entries.append({**entry, "status": "DOWNLOAD_FAILED", "detail": str(exc)})
            continue
        check = check_file(data, ref.file_name, settings.max_file_bytes)
        saved_as = f"{ref.source.value.lower()}_{ref.file_ref}_{_safe(ref.file_name)}"
        (files_dir / saved_as).write_bytes(data)
        entries.append({**entry, "saved_as": f"files/{saved_as}", "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "mime_type": check.mime_type,
                        "status": "OK" if check.ok else "UNSUPPORTED", "detail": check.reason})

    manifest = {"account_id": account_id, "account_name": account.get("Account_Name"),
                "fetched_at": datetime.now(UTC).isoformat(), "files": entries}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download an Account's Zoho data and files")
    parser.add_argument("account_id")
    parser.add_argument("--out", default="samples")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)
    try:
        manifest = fetch(args.account_id, Path(args.out), Path(args.env))
    except ZohoError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    ok = sum(1 for f in manifest["files"] if f["status"] == "OK")
    print(f"OK  {manifest['account_name']}: {len(manifest['files'])} file(s), {ok} usable")
    for f in manifest["files"]:
        print(f"    [{f['status']}] {f['source']:<16} {f.get('file_name')}")
    print(f"    saved to {Path(args.out) / args.account_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
