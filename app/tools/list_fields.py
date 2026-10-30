"""Export the fields of Zoho modules (API name, label, type, picklist values) - no record data.

    python scripts/list_fields.py --env .env.production
    python scripts/list_fields.py --env .env.production --module Accounts --module Contacts

Writes zoho_fields_<org>.md (readable table) and .json next to it, and prints a summary.
Only field definitions are read (needs ZohoCRM.settings.fields.READ); no customer data.
"""

import argparse
import json
import sys
from pathlib import Path

from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings

DEFAULT_MODULES = ["Accounts", "Contacts"]


def describe(f: dict) -> dict:
    lookup = f.get("lookup") or {}
    return {
        "api_name": f.get("api_name"), "label": f.get("field_label"),
        "type": f.get("data_type"), "mandatory": bool(f.get("system_mandatory")),
        "length": f.get("length"),
        "picklist": [p.get("actual_value") for p in (f.get("pick_list_values") or [])
                     if p.get("actual_value") not in (None, "-None-")],
        "lookup_module": (lookup.get("module") or {}).get("api_name") if lookup else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export Zoho module fields (definitions only)")
    ap.add_argument("--env", type=Path, default=Path(".env"))
    ap.add_argument("--module", action="append", help="module API name (repeatable)")
    ap.add_argument("--out", type=Path, default=None, help="output file stem")
    a = ap.parse_args(argv)
    settings = build_settings(read_env(a.env))
    client = build_client(settings)
    org = "sandbox" if "sandbox" in settings.zoho_api_domain else "production"
    stem = a.out or Path(f"zoho_fields_{org}")
    result, lines = {}, [f"# Zoho fields ({org}, {settings.zoho_api_domain})", ""]
    for module in a.module or DEFAULT_MODULES:
        fields = sorted((describe(f) for f in client.get_fields(module)),
                        key=lambda x: (x["label"] or "").lower())
        result[module] = fields
        lines += [f"## {module} ({len(fields)} fields)", "",
                  "| Label | API name | Type | Values / lookup |", "| --- | --- | --- | --- |"]
        for f in fields:
            extra = ", ".join(f["picklist"][:25]) + (" ..." if len(f["picklist"]) > 25 else "")
            if f["lookup_module"]:
                extra = f"-> {f['lookup_module']}"
            lines.append(f"| {f['label']} | {f['api_name']} | {f['type']}"
                         f"{' (mandatory)' if f['mandatory'] else ''} | {extra} |")
        lines.append("")
        print(f"{module}: {len(fields)} fields")
    md, js = stem.with_suffix(".md"), stem.with_suffix(".json")
    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {md} and {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
