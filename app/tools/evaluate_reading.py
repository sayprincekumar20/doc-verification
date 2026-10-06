"""Phase 1E: measure reading + classification against a hand-labeled gold set.

    python scripts/evaluate_reading.py --gold samples/gold.json --files samples

gold.json (keep it in samples/: it contains real customer data and is git-ignored):
{
  "IMG_20260113_092731.jpg": {
    "document_type": "DTI_BN_CERT",
    "fields": {"business_name": "...", "bn_number": "...", "valid_to": "11 May 2026"}
  }
}
Field values are written exactly as printed on the document.

Reports per document: classification right/wrong, and how many true values appear in the text
the engine read ("grounding recall"). The extraction step can only be cross-checked against
values that appear here, so this is the ceiling for grounded extraction.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from app.pipeline.file_checks import check_file
from app.reading.classify import classify_text
from app.reading.pipeline import read_document


def _n(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def evaluate(gold: dict, roots: list[Path]) -> dict:
    index = {p.name: p for root in roots for p in root.rglob("*") if p.is_file()}
    results = []
    for name, expected in gold.items():
        path = index.get(name)
        if path is None:
            results.append({"file": name, "error": "file not found"})
            continue
        data = path.read_bytes()
        check = check_file(data, name, 100 * 1024 * 1024)
        started = time.monotonic()
        pages = read_document(data, check.mime_type)
        text = "\n".join(p.grounding_text for p in pages)
        cls = classify_text(text, name)
        fields = expected.get("fields", {})
        found = {k: _n(v) in _n(text) for k, v in fields.items() if _n(v)}
        results.append({
            "file": name,
            "expected_type": expected.get("document_type"),
            "predicted_type": cls.document_type,
            "type_correct": cls.document_type == expected.get("document_type"),
            "fields_found": sum(found.values()),
            "fields_total": len(found),
            "missing": [k for k, ok in found.items() if not ok],
            "quality": [p.quality.label for p in pages],
            "ocr_conf": [p.quality.ocr_conf for p in pages],
            "seconds": round(time.monotonic() - started, 1),
        })
    ok = [r for r in results if "error" not in r]
    found = sum(r["fields_found"] for r in ok)
    total = sum(r["fields_total"] for r in ok)
    return {
        "documents": len(results),
        "classification_accuracy": round(sum(r["type_correct"] for r in ok) / max(len(ok), 1), 3),
        "grounding_recall": round(found / max(total, 1), 3),
        "fields_found": found,
        "fields_total": total,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate reading + classification on a gold set")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--files", nargs="+", required=True, type=Path,
                        help="folders to search for the gold files (searched recursively)")
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    report = evaluate(json.loads(args.gold.read_text(encoding="utf-8")), args.files)
    for r in report["results"]:
        if "error" in r:
            print(f"ERR   {r['file']}: {r['error']}")
            continue
        mark = "OK " if r["type_correct"] else "BAD"
        print(f"{mark}  {r['file'][:40]:40} {r['predicted_type']:14} "
              f"{r['fields_found']:2}/{r['fields_total']:<2} {r['quality']} {r['seconds']}s"
              + (f"  missing: {', '.join(r['missing'])}" if r["missing"] else ""))
    print(f"\nClassification accuracy: {report['classification_accuracy']:.0%}   "
          f"Grounding recall: {report['fields_found']}/{report['fields_total']} "
          f"({report['grounding_recall']:.0%})")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
