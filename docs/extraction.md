# Phase 1C: key-value extraction

After reading, each document goes to a vision model (`EXTRACTION_PROVIDER` = `anthropic` or
`openai`) with **only the cleaned page images**. The OCR text is deliberately not sent, so the
model and OCR are two independent readings that check each other.

For every field the engine stores: value exactly as printed, normalized value (ISO date, TIN
`NNN-NNN-NNN-NNNNN`, amount), evidence text, page, grounding against OCR, confidence, and the
Zoho field it maps to (`app/extraction/fields.py`).

## Grounding (cross-check against OCR)

| Status | Meaning | Confidence (GOOD page) |
|---|---|---|
| EXACT | value (or a known rendering, e.g. 2021-05-11 = "11 May 2021") is in the OCR text | 0.95 |
| FUZZY | names/addresses only: close match, OCR typo | 0.80 |
| CROSS_CHECKED | confirmed by a rule instead of OCR (BIR: TIN branch code 00000 = Head Office) | 0.90 |
| NOT_FOUND | OCR can't confirm it (often low-res tables): reviewer checks the image | 0.50 |
| UNVERIFIABLE | checkbox field: every option is printed, OCR can't tell which is marked | 0.50 |
| CONFLICT | TIN/code/number/date/money: OCR read something close but different; also any text field whose numbers disagree ("BLOCK 21" vs "BLOCK 23") | 0.30 |

Confidence is multiplied by page quality (FAIR 0.9, POOR 0.75). Invalid TIN/date/amount → 0.2.
The model's own confidence is not used: it is not calibrated.

Measured on the 7 real documents with simulated model output: all 35 injected one-character
errors in TINs/numbers/dates were flagged (22 CONFLICT, 13 NOT_FOUND); none looked confirmed.

## Validation

Required fields, TIN/date/amount formats, DTI 5-year validity, Mayor's permits valid to Dec 31 of
the permit year, expiry against today's date in Manila → `EXPIRED` (CRITICAL). If the model says
the document is a different type than OCR classification, it is re-extracted with the right field
list and `TYPE_MISMATCH` is raised for the reviewer.

## Caching and cost

Results are stored per file content + `extraction_version` (prompt version + provider/model +
reading version): an unchanged file is never sent to the model twice. Images are sent at most
1568 px. Token usage is stored per document.

## OpenAI setup

```
EXTRACTION_PROVIDER=openai
OPENAI_API_KEY=sk-...
EXTRACTION_MODEL=gpt-6.1-sol        # or gpt-6-luna (cheapest), gpt-6-astra (most capable)
EXTRACTION_REASONING_EFFORT=low     # "none" is allowed for gpt-6-luna only
```

The adapter uses OpenAI's Responses API with a strict JSON schema, `store: false` (documents are
not kept on OpenAI's side), no temperature (reasoning models don't accept it) and an 8000-token
output limit (reasoning tokens count toward it). An `incomplete` reply is reported as a failure,
never half-parsed.

## Benchmark result (7 real documents, 71 fields, 2026-10-06)

| Model | Correct | Wrong but looked confirmed | Tokens in / out |
|---|---|---|---|
| gpt-6.1-sol | 65/71 (92%) | **0** | 21.5k / 3.8k |
| gpt-6-luna | 65/71 (92%) | 3 (incl. Head Office vs Branch) | 21.5k / 4.1k |

**Chosen: `gpt-6.1-sol`** (about US$0.035 per 3-document verification). Fixes made from this run:
checkbox fields marked UNVERIFIABLE + BIR branch-code cross-check (BRANCH_MISMATCH is CRITICAL);
changed numbers inside addresses/names are CONFLICT, a missing postal code is not; "No." labels
stripped from codes; prompt rules for labels, long digit strings and checkboxes (`ph-docs-2`).

## Document types

BIR 2303, DTI Business Name certificate, Mayor's/Business Permit and government IDs (BIR TIN ID,
PhilSys, driver's license, UMID, passport, PRC, postal, voter's ID) have their own field lists;
other documents get a generic list. TIN IDs have no expiry (`NO_EXPIRY`); other IDs are checked
against their expiry date. BIR OCNs must match `NNN RC + 14 digits` and start with the RDO code.

Prompt `ph-docs-3` rules learned from real photos: never take the mayor/signing official as the
owner; ignore camera date stamps and phone watermarks; read the clearest copy of repeated pages.

## Choose a provider: benchmark on the gold set

```bash
python scripts/evaluate_extraction.py --gold samples/gold.json --files samples --provider anthropic --out samples/anthropic.json
python scripts/evaluate_extraction.py --gold samples/gold.json --files samples --provider openai --model gpt-6-luna --out samples/luna.json
python scripts/evaluate_extraction.py --gold samples/gold.json --files samples --provider openai --model gpt-6.1-sol --out samples/sol.json
```

Compare field accuracy, **wrong but looked confirmed** (must be 0 or near), and tokens (cost).
