"""Check the "Document Verifications" review module in Zoho (read-only).

    python scripts/check_zoho_setup.py --env .env.sandbox

Compares the module, its fields, the "Proposed Changes" subform and every picklist with
app/zoho/review_module.py, prints what is missing or wrong, and saves the API names Zoho
generated to zoho/review_module_api_names.json (used by the engine code).
"""

import argparse
import json
import sys
from pathlib import Path

from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho import review_module as spec
from app.zoho.errors import ZohoError


def _norm(label: str) -> str:
    return " ".join(str(label).lower().split())


def check_fields(fields: list[dict], expected: list[spec.FieldDef], where: str
                 ) -> tuple[list[str], dict[str, str], dict]:
    problems, names, by_label = [], {}, {_norm(f.get("field_label", "")): f for f in fields}
    for fd in expected:
        f = by_label.get(_norm(fd.label))
        if f is None:
            problems.append(f"MISSING field '{fd.label}' ({fd.kind}) in {where}")
            continue
        names[fd.label] = f.get("api_name")
        dtype = f.get("data_type")
        if dtype not in spec.TYPES[fd.kind]:
            problems.append(f"WRONG TYPE '{fd.label}' in {where}: is {dtype}, "
                            f"should be {fd.kind}")
        if fd.lookup_module:
            target = ((f.get("lookup") or {}).get("module") or {}).get("api_name")
            if target != fd.lookup_module:
                problems.append(f"WRONG LOOKUP '{fd.label}': points to {target}, should be "
                                f"{fd.lookup_module}")
        if fd.picklist:
            actual = [v.get("actual_value") or v.get("display_value")
                      for v in f.get("pick_list_values") or []]
            for value in fd.picklist:
                if value not in actual:
                    problems.append(f"MISSING picklist value '{value}' in {where}.{fd.label}")
            extra = [v for v in actual if v not in fd.picklist and v != "-None-"]
            if extra:
                problems.append(f"NOTE extra picklist values in {where}.{fd.label}: {extra}")
    return problems, names, by_label


def subform_module(field: dict) -> str | None:
    for key in ("subform", "associated_module"):
        value = field.get(key)
        if isinstance(value, dict):
            module = value.get("module")
            if isinstance(module, dict):
                module = module.get("api_name")
            if module or value.get("api_name"):
                return module or value.get("api_name")
    return field.get("api_name")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Zoho review module setup")
    parser.add_argument("--env", default=".env", type=Path,
                        help="use .env.sandbox (ZOHO_API_DOMAIN=https://sandbox.zohoapis.com)")
    parser.add_argument("--out", default=Path("zoho/review_module_api_names.json"), type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    settings = build_settings(read_env(args.env))
    client = build_client(settings)
    print(f"Zoho: {settings.zoho_api_domain}")
    try:
        modules = client.list_modules()
        module = next((m for m in modules if _norm(m.get("plural_label", "")) ==
                       _norm(spec.MODULE_PLURAL) or _norm(m.get("singular_label", "")) ==
                       _norm(spec.MODULE_SINGULAR)), None)
        if module is None:
            print(f"FAIL  module '{spec.MODULE_PLURAL}' not found. Create it first "
                  "(docs/zoho-review-module.md, step 2).")
            return 1
        api = module["api_name"]
        print(f"OK    module '{spec.MODULE_PLURAL}' -> API name {api}")
        problems, names, by_label = check_fields(client.get_fields(api), spec.MODULE_FIELDS,
                                                 api)
        result = {"module": api, "fields": names, "subform": None, "subform_fields": {}}
        sub = by_label.get(_norm(spec.SUBFORM_LABEL))
        if sub is not None:
            sub_api = subform_module(sub)
            result["subform"] = {"field": sub.get("api_name"), "module": sub_api}
            sub_problems, sub_names, _ = check_fields(client.get_fields(sub_api),
                                                      spec.SUBFORM_FIELDS, sub_api)
            problems += sub_problems
            result["subform_fields"] = sub_names
    except ZohoError as exc:
        print(f"FAIL  {exc}")
        return 1

    for label, name in {**result["fields"], **result["subform_fields"]}.items():
        print(f"      {label:24} -> {name}")
    errors = [p for p in problems if not p.startswith("NOTE")]
    for p in problems:
        print(("      " if p.startswith("NOTE") else "FAIL  ") + p)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved API names to {args.out}")
    print("All checks passed." if not errors else f"{len(errors)} problem(s) to fix in Zoho.")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
