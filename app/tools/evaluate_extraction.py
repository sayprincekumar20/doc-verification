"""Phase 1E: score AI extraction against the hand-labeled gold set.

    python scripts/evaluate_extraction.py --gold samples/gold.json --files samples \
        [--provider anthropic|openai] [--model NAME] [--out samples/extraction_report.json]

Provider/model/keys default to EXTRACTION_PROVIDER, EXTRACTION_MODEL, ANTHROPIC_API_KEY /
OPENAI_API_KEY in .env. Run once per provider to compare them on the same documents.

Per field:   CORRECT | MINOR_DIFF | WRONG | MISSED (value on the document, model returned null)
Most important number: WRONG values whose grounding was EXACT/FUZZY ("wrong but looked
confirmed"); these are the errors a reviewer could miss.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import cv2

from app.extraction.compare import DIFFERENT, MINOR_DIFF, SAME, compare
from app.extraction.extract import PageInput, extract_document
from app.extraction.fields import specs_for
from app.extraction.providers import VisionProvider, build_provider
from app.pipeline.file_checks import check_file
from app.reading.classify import classify_text
from app.reading.pipeline import read_document
from app.tools.envfile import read_env


def _jpeg(image) -> bytes:
    return cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def evaluate(gold: dict, roots: list[Path], provider: VisionProvider, today: date) -> dict:
    index = {p.name: p for root in roots for p in root.rglob("*") if p.is_file()}
    docs, outcome, grounding_of_wrong = [], Counter(), Counter()
    tokens_in = tokens_out = 0
    for name, expected in gold.items():
        path = index.get(name)
        if path is None:
            docs.append({"file": name, "error": "file not found"})
            continue
        data = path.read_bytes()
        started = time.monotonic()
        pages = read_document(data, check_file(data, name, 100 * 1024 * 1024).mime_type)
        doc_type = classify_text("\n".join(p.grounding_text for p in pages), name).document_type
        result = extract_document(
            [PageInput(_jpeg(p.prepared.color), p.grounding_text, p.quality.label)
             for p in pages], doc_type, provider, today)
        tokens_in += result.input_tokens
        tokens_out += result.output_tokens
        kinds = {s.name: s.kind for s in specs_for(result.document_type)}
        rows = []
        for field_name, true_value in expected.get("fields", {}).items():
            if field_name not in kinds:
                continue
            got = result.fields.get(field_name)
            value = got.value if got else None
            verdict = compare(kinds[field_name], value, true_value)
            label = ("MISSED" if value is None else
                     {SAME: "CORRECT", MINOR_DIFF: "MINOR_DIFF", DIFFERENT: "WRONG"}[verdict])
            outcome[label] += 1
            if label == "WRONG":
                grounding_of_wrong[got.grounding] += 1
            rows.append({"field": field_name, "expected": true_value, "got": value,
                         "result": label, "grounding": got.grounding if got else None,
                         "confidence": got.confidence if got else None})
        docs.append({"file": name, "expected_type": expected.get("document_type"),
                     "type": result.document_type,
                     "type_correct": result.document_type == expected.get("document_type"),
                     "validity": result.validity_status, "fields": rows,
                     "seconds": round(time.monotonic() - started, 1)})
    total = sum(outcome.values())
    silent = grounding_of_wrong["EXACT"] + grounding_of_wrong["FUZZY"]
    return {
        "provider": provider.name, "model": provider.model,
        "field_accuracy": round(outcome["CORRECT"] / max(total, 1), 3),
        "outcomes": dict(outcome), "fields_total": total,
        "wrong_by_grounding": dict(grounding_of_wrong),
        "wrong_but_looked_confirmed": silent,
        "input_tokens": tokens_in, "output_tokens": tokens_out, "documents": docs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score AI extraction against a gold set")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--files", nargs="+", required=True, type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--env", default=".env", type=Path)
    parser.add_argument("--today", help="evaluation date (YYYY-MM-DD), default today")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    env = read_env(args.env)
    provider_name = args.provider or env.get("EXTRACTION_PROVIDER", "none")
    key = env.get({"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(
        provider_name, ""), "")
    provider = build_provider(provider_name, key, args.model or env.get("EXTRACTION_MODEL"))
    if provider is None:
        print("Set --provider (or EXTRACTION_PROVIDER in .env) to anthropic or openai.")
        return 2
    today = date.fromisoformat(args.today) if args.today else date.today()
    report = evaluate(json.loads(args.gold.read_text(encoding="utf-8")), args.files, provider,
                      today)

    for d in report["documents"]:
        if "error" in d:
            print(f"ERR  {d['file']}: {d['error']}")
            continue
        counts = Counter(r["result"] for r in d["fields"])
        print(f"{'OK ' if d['type_correct'] else 'BAD'} {d['file'][:38]:38} {d['type']:14} "
              f"correct {counts['CORRECT']}/{len(d['fields'])} {d['validity']:8} {d['seconds']}s")
        for r in d["fields"]:
            if r["result"] != "CORRECT":
                print(f"     {r['result']:10} {r['field']:20} expected={r['expected']!r} "
                      f"got={r['got']!r} grounding={r['grounding']}")
    print(f"\n{report['provider']} / {report['model']}: field accuracy "
          f"{report['outcomes'].get('CORRECT', 0)}/{report['fields_total']} "
          f"({report['field_accuracy']:.0%})  outcomes={report['outcomes']}")
    print(f"Wrong values by grounding: {report['wrong_by_grounding']}   "
          f"wrong but looked confirmed: {report['wrong_but_looked_confirmed']}")
    print(f"Tokens: {report['input_tokens']} in / {report['output_tokens']} out")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
