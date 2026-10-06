"""Try reading + classification + AI extraction on local files (no database, no Zoho).

    python scripts/try_extraction.py samples/<account_id>/files
    python scripts/try_extraction.py some/file.jpg other.pdf --provider anthropic
    python scripts/try_extraction.py samples/<id>/files --gold-template samples/new_gold.json

Prints each document's type, validity, issues and fields (value, normalized, OCR check,
confidence) and saves everything to <folder>/extraction_results.json.

--gold-template drafts gold-set entries from the AI output. They are only a starting point:
check every value against the document and correct it before using it for evaluation,
otherwise you are grading the AI against itself.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import cv2

from app.extraction.extract import PageInput, extract_document
from app.extraction.providers import ExtractionError, build_provider
from app.pipeline.file_checks import check_file
from app.reading.classify import classify_text
from app.reading.normalize import UnreadableFileError
from app.reading.pipeline import read_document
from app.tools.envfile import read_env

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic",
             ".heif", ".docx", ".xlsx", ".doc", ".xls"}


def _files(paths: list[Path]) -> list[Path]:
    found = []
    for p in paths:
        if p.is_dir():
            found += sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED)
        elif p.is_file():
            found.append(p)
    return found


def _jpeg(image) -> bytes:
    return cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def run_file(path: Path, provider, today: date) -> dict:
    data = path.read_bytes()
    check = check_file(data, path.name, 100 * 1024 * 1024)
    if not check.ok:
        return {"file": path.name, "status": "REJECTED", "error": check.reason}
    try:
        pages = read_document(data, check.mime_type)
    except UnreadableFileError as exc:
        return {"file": path.name, "status": "UNREADABLE", "error": str(exc)}
    text = "\n".join(p.grounding_text for p in pages)
    cls = classify_text(text, path.name)
    out = {"file": path.name, "status": "READ", "classified_as": cls.document_type,
           "pages": [{"page": p.page_number, "quality": p.quality.label,
                      "ocr_conf": p.quality.ocr_conf, "rotation": p.prepared.rotation,
                      "reasons": p.quality.reasons} for p in pages]}
    readable = [p for p in pages if p.quality.label != "UNREADABLE"]
    if provider is None or not readable:
        return out
    try:
        r = extract_document([PageInput(_jpeg(p.prepared.color), p.grounding_text,
                                        p.quality.label) for p in readable],
                             cls.document_type, provider, today)
    except ExtractionError as exc:
        return {**out, "status": "EXTRACTION_FAILED", "error": str(exc)}
    return {**out, "status": "EXTRACTED", "document_type": r.document_type,
            "model_type": r.model_type, "validity": r.validity_status,
            "valid_until": r.valid_until, "issues": r.issues,
            "fields": {k: asdict(v) for k, v in r.fields.items()},
            "tokens": {"input": r.input_tokens, "output": r.output_tokens, "calls": r.calls}}


def print_result(r: dict) -> None:
    print(f"\n=== {r['file']}")
    if r["status"] in ("REJECTED", "UNREADABLE", "EXTRACTION_FAILED"):
        print(f"    {r['status']}: {r['error']}")
        if r["status"] != "EXTRACTION_FAILED":
            return
    pages = ", ".join(f"p{p['page']} {p['quality']} ({p['ocr_conf']:.0f}%)" for p in r["pages"])
    print(f"    classified: {r['classified_as']}   pages: {pages}")
    if r["status"] != "EXTRACTED":
        if r["status"] == "READ":
            print("    (no AI provider configured: reading + classification only)")
        return
    print(f"    AI says: {r['model_type']}   validity: {r['validity']}"
          + (f" (until {r['valid_until']})" if r["valid_until"] else ""))
    for name, f in r["fields"].items():
        if f["value"] is None:
            print(f"      {name:22} -")
            continue
        norm = f" -> {f['normalized']}" if f["normalized"] not in (None, f["value"].upper(),
                                                                   f["value"]) else ""
        print(f"      {name:22} {f['value']!s:45.45}{norm}  [{f['grounding']} "
              f"{f['confidence']}]")
    for i in r["issues"]:
        if i["severity"] != "INFO":
            print(f"    ! {i['severity']:8} {i['code']}: {i['message']}")
    print(f"    tokens: {r['tokens']['input']} in / {r['tokens']['output']} out, "
          f"{r['tokens']['calls']} call(s)")


def gold_template(results: list[dict]) -> dict:
    return {r["file"]: {"document_type": r["document_type"], "_CHECK_ME": True,
                        "fields": {k: f["value"] for k, f in r["fields"].items()
                                   if f["value"] is not None}}
            for r in results if r["status"] == "EXTRACTED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Try extraction on local files")
    parser.add_argument("paths", nargs="+", type=Path, help="files or folders")
    parser.add_argument("--provider", help="anthropic | openai | none (default from .env)")
    parser.add_argument("--model")
    parser.add_argument("--env", default=".env", type=Path)
    parser.add_argument("--today", help="date for expiry checks (YYYY-MM-DD), default today")
    parser.add_argument("--gold-template", type=Path,
                        help="write draft gold entries (verify every value by hand!)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    env = read_env(args.env)
    name = args.provider or env.get("EXTRACTION_PROVIDER", "none")
    key = env.get({"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(name, ""))
    try:
        provider = build_provider(name, key, args.model or env.get("EXTRACTION_MODEL"))
    except ExtractionError as exc:
        print(f"FAIL  {exc}")
        return 2
    files = _files(args.paths)
    if not files:
        print("No supported files found.")
        return 1
    today = date.fromisoformat(args.today) if args.today else date.today()
    label = f"{provider.name} / {provider.model}" if provider else "none"
    print(f"{len(files)} file(s); provider: {label}")
    results = []
    for path in files:
        result = run_file(path, provider, today)
        print_result(result)
        results.append(result)

    folder = args.paths[0] if args.paths[0].is_dir() else args.paths[0].parent
    out = folder / "extraction_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out}")
    if args.gold_template:
        args.gold_template.write_text(json.dumps(gold_template(results), indent=2,
                                                 ensure_ascii=False), encoding="utf-8")
        print(f"Draft gold entries: {args.gold_template} - check EVERY value against the "
              "documents, fix mistakes, delete \"_CHECK_ME\", then merge into samples/gold.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
