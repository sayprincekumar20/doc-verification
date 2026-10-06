# Document Verification Engine (Option E)

FastAPI engine that reads customer documents from Zoho CRM. Reviewers approve its results inside
Zoho CRM. This release covers **Phase 0 (foundation)**, **Phase 1A (document collection)** and
**Phase 1B (reading: format conversion, photo cleanup, OCR, classification)**, **Phase 1C
(key-value extraction with a vision model, grounded against OCR)** and the Phase 1E evaluation
tools. See `docs/reading.md` and `docs/extraction.md`.

## What works now

- `POST /v1/jobs` starts a verification for an Account (called later by the CRM "Verify Account" button).
- A background worker collects every document from three Zoho sources:
  1. the Account's **Attachments** related list,
  2. the Account's **fileupload fields** (`BIR_Registration_COR`, `Business_registration`,
     `Business_Permit`, `General_Information_Sheet`),
  3. files attached to the Account's **Notes**.
- Each file is checked by its real content (PDF, JPG, PNG, WEBP, TIFF, BMP, HEIC, DOCX, XLSX, DOC, XLS),
  hashed with SHA-256, and stored once. Duplicates and unsupported files are recorded, not dropped.
- One active job per account: a second click returns the running job instead of starting another.
- Zoho calls retry on rate limits (429) and server errors, and refresh the access token automatically.
- Logs are JSON and mask TINs and OAuth tokens.

Next: Phase 1D (cross-document checks and proposed CRM changes vs the Account snapshot).

## Test on a customer

See `docs/testing-a-customer.md`: fetch an Account's documents, run `scripts/try_extraction.py`,
label results into the gold set, measure with `scripts/evaluate_extraction.py`.

## Zoho side

- `docs/zoho-api.md`: the exact Zoho API calls the engine makes.
- `zoho/client_script_verify_button.js`: the "Verify Account" button (Client Script).
- `zoho/deluge_dv_check_documents.dg`, `zoho/deluge_dv_trigger_verification.dg`: Deluge functions the
  button calls. Create org variables `dv_engine_url` and `dv_engine_api_key` first.

## What a job records

- The button payload as sent (reason, reported counts, who clicked and when).
- `account_snapshot`: the Account fields read from Zoho at that moment (names, status, address,
  TIN, business type, ...). Later phases compare documents against these.
- `warnings`: non-fatal issues such as `CUSTOMER_NUMBER_MISMATCH`, `ATTACHMENT_COUNT_MISMATCH`,
  `DOCUMENT_STATUS_MISMATCH`.
- `documents`: every file found, with source, hash, type and status.

## Project layout

```
app/
  main.py              FastAPI app
  config.py            settings from environment variables
  logging.py           JSON logs + sensitive-data masking
  schemas.py           API request/response models
  api/                 jobs + health endpoints, API-key auth
  db/                  SQLAlchemy models and sessions
  zoho/                OAuth token provider and CRM client
  storage/             local disk or S3-compatible storage
  pipeline/            collect.py (Phase 1A), file_checks.py
  services/            job creation, audit events
  workers/             Celery app and tasks
migrations/            Alembic migrations
tests/                 pytest suite with a fake Zoho CRM
```

## 1. Connect to Zoho CRM (one time)

Full guide: `docs/zoho-auth.md`. In short:

```bash
cp .env.example .env
python scripts/zoho_auth.py scopes          # scopes to paste in the API Console
# API Console > Self Client: put Client ID/Secret in .env, generate a grant code
python scripts/zoho_auth.py exchange-code   # saves ZOHO_REFRESH_TOKEN to .env
python scripts/zoho_auth.py test --account-id <account_id>
python scripts/fetch_account.py <account_id>   # sample data -> samples/ (git-ignored)
```

Postman: import the collection and environment from `postman/`; tokens refresh automatically.

## 2. Run locally

```bash
cp .env.example .env        # fill in API_KEY and the ZOHO_* values
docker compose up --build
```

This starts Postgres, Redis, runs migrations, then starts the API (port 8000) and a worker.

Start a job with the same payload the CRM button sends (save it as `payload.json`):

```json
{
  "account_id": "1000000000000001",
  "customer_number": "EXAMPLE-001",
  "reason": "Routine verification request",
  "document_status": "YES",
  "attachment_count": 3,
  "requested_by": {"id": "1000000000000003", "email": "reviewer@example.com"},
  "requested_at": "2026-10-05T15:36:00+08:00",
  "source": "crm_button"
}
```

```bash
curl -X POST http://localhost:8000/v1/jobs \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d @payload.json
```

| Field | Required | Notes |
|---|---|---|
| account_id | yes | Zoho Account record id |
| customer_number | no | `Inventory_Customer_Number`; filled from Zoho if blank, warning if different |
| reason | no | Free text, may be blank (max 500 chars) |
| document_status | yes | `YES` / `NO`, what the button saw |
| attachment_count | no | Files the button counted; warning if the engine finds a different number |
| requested_by | no | `{"id", "email"}`; a string like `"<user id> <email>"` is also accepted |
| requested_at | no | ISO time; without an offset it is treated as Manila time (+08:00) |
| source | no | `crm_button` (default), `manual`, `schedule`, `api` |

Replies: `202` with `created: true` for a new job, or `200` with `created: false` and the running
job's id if the account already has one in progress.

Check it:

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/v1/jobs/<job_id>
```

Check the engine's Zoho connection:

```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/health/zoho
```

API docs (non-prod only): http://localhost:8000/docs

## 3. Run tests

```bash
pip install -e ".[dev]"
ruff check . && pytest -q
```

Tests use SQLite and a fake Zoho CRM; no network or credentials needed.

## 4. Production (Singapore VPS + Supabase)

- Create a Supabase project in the Singapore region. Set `DATABASE_URL` to its Postgres connection
  string (`postgresql+psycopg://...`).
- Create a private Storage bucket, enable S3 access, and set `STORAGE_BACKEND=s3`,
  `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
- On the VPS run only `api`, `worker` and `redis` (remove `postgres` from compose), behind HTTPS
  (e.g. Caddy or Nginx with Let's Encrypt). Allow only ports 80/443 in the firewall.
- Run `alembic upgrade head` on each deploy before starting the new version.
- Set `APP_ENV=prod` (hides /docs) and optionally `SENTRY_DSN`.

## Job statuses

| Status | Meaning |
|---|---|
| QUEUED | Created, waiting for a worker |
| COLLECTING | Downloading documents from Zoho |
| COLLECTED | At least one usable document stored; reading queued |
| READING | OCR + classification running |
| READ | Every stored file has pages, text, quality and document type (final if `EXTRACTION_PROVIDER=none`) |
| EXTRACTING | Vision model extracting fields |
| EXTRACTED | Every document has normalized, grounded, validated fields |
| NO_DOCUMENTS | Nothing usable on the account |
| FAILED | Zoho unreachable after retries, auth problem, or unexpected error (see `error`) |

## To verify against your Zoho org

These follow Zoho's v8 API conventions but should be confirmed with one real account:

- The shape of Note `$attachments` items and the Notes attachment download path
  (`/Notes/{note_id}/Attachments/{attachment_id}`).
- Fileupload field values carry `attachment_Id` and download via
  `/Accounts/{id}/actions/download_fields_attachment` (fallback `/files?id={File_Id__s}`).
- Attachments list field names (`File_Name`, `Created_By`, ...), copied from your working request.
