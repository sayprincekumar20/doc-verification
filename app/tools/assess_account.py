"""Run the whole engine on one downloaded customer, without the database or Zoho writes.

    python scripts/fetch_account.py <account_id>          (Windows, .venv)
    docker compose run --rm --no-deps api python scripts/assess_account.py samples/<account_id>

Uses samples/<id>/account.json (the Zoho Account) and samples/<id>/files/. Prints the
recommendation, reasons, required documents, cross-document checks and the proposed Zoho
changes; saves samples/<id>/assessment.json.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app.assessment.alerts import build_alerts, status_decision
from app.assessment.assess import DocInput, assess
from app.assessment.auto_apply import plan
from app.config import DEFAULT_SNAPSHOT_FIELDS
from app.extraction.providers import ExtractionError, build_provider
from app.tools.envfile import read_env
from app.tools.try_extraction import _files, print_result, run_file

_ICONS = {"FILL": "+", "CORRECT": "~", "MATCH": "=", "HOLD": "‖", "REVIEW_CONFLICT": "?",
          "DIFFERS": "≠", "ATTACH": "@", "NO_PICKLIST_VALUE": "?"}


def print_assessment(a: dict) -> None:
    print(f"\n================ RECOMMENDATION: {a['recommendation']}  "
          f"(business form: {a['business_form']}, rules {a['rules_version']})")
    for r in a["reasons"]:
        print(f"  - {r}")
    print("\nRequired documents:")
    for r in a["requirements"]:
        until = f" (until {r['valid_until']})" if r.get("valid_until") else ""
        print(f"  {r['status']:10} {r['document_type']:14} {r.get('file') or ''}{until}")
    if a["cross_document"]:
        print("\nDocuments vs each other:")
        for c in a["cross_document"]:
            values = " | ".join(f"{v['type']}: {v['value']}" for v in c["values"])
            print(f"  {c['status']:18} {c['attribute']:15} {values}")
            if c.get("note"):
                print(f"  {'':18} note: {c['note']}")
    print("\nProposed Zoho changes (+ fill, ~ correct, = match, ‖ on hold, ? reviewer decides,"
          " ≠ differs, @ attach):")
    for p in a["proposals"]:
        flag = "  needs attention" if p["needs_attention"] else ""
        conf = f" [{p['confidence']}]" if p["confidence"] is not None else ""
        print(f"  {_ICONS.get(p['action'], ' ')} {p['action']:17} {p['zoho_field']:30} "
              f"{str(p['current_value'])!s:.30} -> {str(p['proposed_value'])!s:.45}{conf}{flag}")
        if p["action"] in ("CORRECT", "HOLD", "DIFFERS", "REVIEW_CONFLICT", "NO_PICKLIST_VALUE"):
            print(f"  {'':19} {p['reason']}")
    for d in a.get("not_extracted", []):
        print(f"  ! not extracted: {d['file']} ({d['status']}) {d.get('error') or ''}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess one downloaded customer end to end")
    parser.add_argument("folder", type=Path, help="samples/<account_id>")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--env", default=".env", type=Path)
    parser.add_argument("--today", help="YYYY-MM-DD for expiry checks, default today")
    parser.add_argument("--quiet", action="store_true", help="don't print per-document fields")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    account_file = args.folder / "account.json"
    if not account_file.exists():
        print(f"{account_file} not found. Run fetch_account.py for this account first.")
        return 1
    account = json.loads(account_file.read_text(encoding="utf-8"))
    snapshot = {k: account.get(k) for k in DEFAULT_SNAPSHOT_FIELDS}

    env = read_env(args.env)
    name = args.provider or env.get("EXTRACTION_PROVIDER", "none")
    key = env.get({"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(name, ""))
    try:
        provider = build_provider(name, key, args.model or env.get("EXTRACTION_MODEL"),
                                  reasoning_effort=env.get("EXTRACTION_REASONING_EFFORT", "low"))
    except ExtractionError as exc:
        print(f"FAIL  {exc}")
        return 2
    if provider is None:
        print("An AI provider is required (EXTRACTION_PROVIDER in .env).")
        return 2
    today = date.fromisoformat(args.today) if args.today else date.today()

    print(f"{account.get('Account_Name')} ({account.get('id')}): reading and extracting...")
    results, inputs = [], []
    for i, path in enumerate(_files([args.folder / "files"])):
        r = run_file(path, provider, today)
        results.append(r)
        if not args.quiet:
            print_result(r)
        if r["status"] == "EXTRACTED":
            inputs.append(DocInput(str(i), r["file"], r["document_type"], r["fields"],
                                   r["validity"], r["valid_until"], r["issues"]))
    a = assess(snapshot, inputs, today)
    a["not_extracted"] = [{"file": r["file"], "status": r["status"], "error": r.get("error")}
                          for r in results if r["status"] != "EXTRACTED"]
    print_assessment(a)
    threshold = float(env.get("AUTO_APPLY_THRESHOLD") or 0.95)
    auto_plan = plan(a, threshold)
    a["auto_apply_plan"] = auto_plan
    print(f"\nAutomatic update plan (threshold {threshold}; this tool never writes to Zoho; "
          f"AUTO_APPLY_MODE in .env is {env.get('AUTO_APPLY_MODE', 'off')}):")
    if not auto_plan:
        print("  nothing to fill or correct")
    for item in auto_plan:
        print(f"  {item['decision']:6} {item['action']:7} {item['zoho_field']:30} -> "
              f"{str(item['new_value'])!s:.40}  ({item['reason']})")
    status = status_decision(a, snapshot.get("Customer_Status"), threshold)
    print(f"\nCustomer_Status: now {snapshot.get('Customer_Status')!r} -> {status[0]}"
          f"  ({status[1]})")
    alerts = build_alerts(a, today)
    a["alerts"] = alerts
    a["status_decision"] = {"decision": status[0], "reason": status[1]}
    print("\nAlerts (Note on the Account + Task for the owner when AUTO_APPLY_MODE=on):")
    if not alerts:
        print("  none")
    for al in alerts:
        action = f"  -> {al['action']}" if al.get("action") else ""
        print(f"  [{al['severity']}] {al['message']}{action}")
    out = args.folder / "assessment.json"
    out.write_text(json.dumps({"assessment": a, "documents": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
