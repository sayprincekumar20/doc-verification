# Phase 1B: reading documents (and Phase 1E evaluation)

After collection, the worker reads every stored file (`read_documents` task):

1. **Normalize** to page images (`app/reading/normalize.py`)
   - Images: EXIF rotation applied (phone photos), transparency flattened, multi-page TIFF split.
   - PDF: each page rendered at 300 DPI. Pages with a real text layer use it directly (no OCR);
     scanned PDFs (e.g. from office copiers) are OCR'd.
   - DOCX/XLSX/DOC/XLS: converted to PDF with LibreOffice first.
   - Password-protected or corrupt files are marked `UNREADABLE` with a reason.
2. **Clean up** (`preprocess.py`)
   - Orientation: Tesseract OSD when confident, otherwise each of the 4 rotations is tried and the
     one with the most confidently read words wins (OSD guessed wrong on a sideways permit photo).
   - Document edge crop + perspective correction, **camera photos only** (it cut table cells on scans).
   - Deskew (±5°), resize to 2400 px long side.
3. **OCR** (`ocr.py`): Tesseract on plain grayscale. When mean confidence is below 80, a second
   pass reads only the dark ink, which removes the BIR "BUREAU OF INTERNAL REVENUE" background.
   Both texts are kept as `grounding_text`, used later to cross-check AI-extracted values.
4. **Quality** (`quality.py`): GOOD / FAIR / POOR / UNREADABLE from OCR confidence, sharpness and
   resolution, with reasons (e.g. "low resolution").
5. **Classify** (`classify.py`): keyword evidence for BIR_2303, DTI_BN_CERT, MAYORS_PERMIT,
   SEC_CERT, GIS, BARANGAY_CLEARANCE, GOVERNMENT_ID, FOOD_SAFETY_PERMIT, else OTHER. File names
   are only a weak hint.

Results are stored per **file content** (`file_readings`, `file_pages`, keyed by SHA-256 +
`PIPELINE_VERSION`), with the cleaned page image saved to storage. The same file is never read
twice, even across accounts. Bump `PIPELINE_VERSION` in `app/pipeline/read.py` when the reading
logic changes.

## Measured on 7 real documents (3 customers)

Gold set: 71 hand-checked values. "Found" = the true value appears in the engine's text.

| Document | Format | Found |
|---|---|---|
| Mayor's permit | phone photo, stored sideways | 10/11 |
| BIR 2303 | phone photo, watermark background | 10/11 |
| DTI certificate | phone photo | 7/7 |
| Mayor's permit | low-resolution scan, table layout | 6/13 |
| DTI certificate | low-resolution scan | 6/7 |
| BIR 2303 | low-resolution screenshot | 10/11 |
| BIR 2303 (corporation, branch) | scanned PDF, no text layer | 11/11 |

Classification: 7/7 (also 7/7 with meaningless file names). Grounding recall: 60/71 (84%).
Tuning history: 33/71 with denoise+contrast (removed), 51 plain, 58 +crop-only-photos, 60 +ink pass.

OCR alone is not the extractor: low-resolution tables and misreads (e.g. "14 May" for "11 May")
are why Phase 1C uses a vision model, with this text as the cross-check.

## Run the evaluation

Keep the gold file and documents in `samples/` (git-ignored, real customer data):

```bash
python scripts/evaluate_reading.py --gold samples/gold.json --files samples --out samples/report.json
# or, on PCs that block compiled libraries:
docker compose run --rm api python scripts/evaluate_reading.py --gold samples/gold.json --files samples
```

Add every new real document to the gold set (aim for 50-100, weighted to phone photos).
