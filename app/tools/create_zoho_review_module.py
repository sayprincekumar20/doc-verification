"""Create the "Document Verifications" review module in a Zoho CRM **sandbox**.

    python scripts/create_zoho_review_module.py --env .env.sandbox            # dry run
    python scripts/create_zoho_review_module.py --env .env.sandbox --apply    # create

Creates (only what is missing, so it is safe to run again):
  1. the module, with "DV Number" (auto-number DVnnn) as its name field
  2. all module fields (labels, types, Account lookup, exact picklist values)
  3. the "Proposed Changes" subform columns - after you create the subform in Zoho with its
     first column ("Zoho Field"); Zoho's API cannot create subforms themselves.
Then run scripts/check_zoho_setup.py to confirm.

Refuses to run against production unless --allow-production is given.
Needs the setup scopes: python scripts/zoho_auth.py scopes --setup
"""

import argparse
import sys
from pathlib import Path

from app.tools.check_zoho_setup import _norm, subform_module
from app.tools.envfile import read_env
from app.tools.zoho_auth import build_client, build_settings
from app.zoho import review_module as spec
from app.zoho.errors import ZohoError

BATCH = 5  # Zoho: max 5 fields per Create Custom Field call


def _results_ok(results: list[dict], labels: list[str]) -> list[str]:
    problems = []
    for label, r in zip(labels, results, strict=False):
        if r.get("code") == "SUCCESS":
            print(f"      created  {label}")
        else:
            problems.append(f"{label}: {r.get('code')} {r.get('message')} {r.get('details')}")
            print(f"FAIL  {label}: {r.get('code')} - {r.get('message')} {r.get('details')}")
    return problems


def _create_fields(client, module: str, todo: list[spec.FieldDef], module_ids: dict,
                   apply: bool) -> list[str]:
    problems = []
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        if not apply:
            for fd in batch:
                print(f"      would create  {fd.label} ({fd.kind})"
                      + (f": {', '.join(fd.picklist)}" if fd.picklist else ""))
            continue
        payload = [spec.field_payload(fd, module_ids) for fd in batch]
        try:
            results = client.create_fields(module, payload)
        except ZohoError as exc:
            problems.append(str(exc))
            print(f"FAIL  creating {[f.label for f in batch]}: {exc}")
            continue
        problems += _results_ok(results, [fd.label for fd in batch])
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the Zoho review module (sandbox)")
    parser.add_argument("--env", default=".env.sandbox", type=Path)
    parser.add_argument("--apply", action="store_true", help="really create (default: dry run)")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--profile", action="append",
                        help="profile name(s) that can use the module (default: Administrator)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    settings = build_settings(read_env(args.env))
    if "sandbox" not in settings.zoho_api_domain and not args.allow_production:
        print(f"STOP  {settings.zoho_api_domain} is not a sandbox. Set ZOHO_API_DOMAIN="
              "https://sandbox.zohoapis.com in the env file (or pass --allow-production).")
        return 2
    client = build_client(settings)
    mode = "APPLY" if args.apply else "DRY RUN (nothing is changed; add --apply)"
    print(f"Zoho: {settings.zoho_api_domain}   mode: {mode}")
    problems: list[str] = []

    try:
        modules = client.list_modules()
        module_ids = {m.get("api_name"): m.get("id") for m in modules}
        module = next((m for m in modules if _norm(m.get("plural_label", "")) ==
                       _norm(spec.MODULE_PLURAL)), None)

        # 1. module
        if module is None:
            wanted = [p.lower() for p in (args.profile or ["Administrator"])]
            profiles = [p for p in client.list_profiles()
                        if (p.get("name") or "").lower() in wanted]
            if not profiles:
                print(f"FAIL  no profile named {args.profile or ['Administrator']} found")
                return 1
            print(f"1. Module '{spec.MODULE_PLURAL}': missing -> "
                  + ("creating" if args.apply else "would create")
                  + f" (profiles: {', '.join(p['name'] for p in profiles)}; name field "
                    "'DV Number' auto-number DV1, DV2...)")
            if not args.apply:
                print("2. Fields: all would be created after the module exists.")
                for fd in spec.MODULE_FIELDS:
                    if fd.kind not in ("autonumber", "subform"):
                        print(f"      would create  {fd.label} ({fd.kind})")
                print("3. Subform: create it in Zoho after the module (see below).")
                return 0
            results = client.create_module(spec.module_payload([p["id"] for p in profiles]))
            if not results or results[0].get("code") != "SUCCESS":
                print(f"FAIL  module not created: {results}")
                return 1
            print("      module created")
            modules = client.list_modules()
            module = next(m for m in modules if _norm(m.get("plural_label", "")) ==
                          _norm(spec.MODULE_PLURAL))
        else:
            print(f"1. Module '{spec.MODULE_PLURAL}' exists ({module['api_name']})")
        api = module["api_name"]

        # 2. module fields
        existing = {_norm(f.get("field_label", "")): f for f in client.get_fields(api)}
        todo = [fd for fd in spec.MODULE_FIELDS
                if fd.kind not in ("autonumber", "subform") and _norm(fd.label) not in existing]
        print(f"2. Module fields: {len(todo)} missing")
        problems += _create_fields(client, api, todo, module_ids, args.apply)

        # 3. subform columns
        sub = existing.get(_norm(spec.SUBFORM_LABEL))
        if sub is None:
            print(f"3. Subform '{spec.SUBFORM_LABEL}' not found. In Zoho: Setup > Modules and "
                  f"Fields > {spec.MODULE_PLURAL} > Standard layout > drag 'Subform' into the "
                  f"layout, name it '{spec.SUBFORM_LABEL}', add one Single Line column "
                  f"'Zoho Field', Save. Then run this script again.")
        else:
            sub_api = subform_module(sub)
            sub_existing = {_norm(f.get("field_label", "")) for f in client.get_fields(sub_api)}
            sub_todo = [fd for fd in spec.SUBFORM_FIELDS if _norm(fd.label) not in sub_existing]
            print(f"3. Subform '{spec.SUBFORM_LABEL}' exists ({sub_api}): "
                  f"{len(sub_todo)} column(s) missing")
            problems += _create_fields(client, sub_api, sub_todo, module_ids, args.apply)
    except ZohoError as exc:
        print(f"FAIL  {exc}")
        return 1

    if problems:
        print(f"\n{len(problems)} problem(s). Paste this output to get them fixed.")
        return 1
    print("\nDone." + ("" if args.apply else " (dry run)") +
          " Next: python scripts/check_zoho_setup.py --env " + str(args.env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
