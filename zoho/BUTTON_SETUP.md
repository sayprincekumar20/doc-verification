# "Verify Account" button (Phase 2) - setup and sandbox test

Click flow: **Verify Account** (Accounts detail page) -> optional reason -> Deluge
`dv_verify_account` reads the Account, Attachments and Notes once and checks for documents
(none -> message, engine not called) -> **one** request to the engine with everything ->
engine downloads only the files, reads, extracts, assesses -> creates the
**Document Verifications** record (visible in the Account's related list) with alerts,
proposals and page images.

## 1. Engine reachable from Zoho

**Production:** the one-server setup (`docs/deployment.md`), e.g. `https://dv.yourcompany.com`.

**Sandbox test from your PC** (before the server exists):

1. In `.env.sandbox` add the engine settings used by the full service (copy from `.env`):
   `API_KEY`, `DATABASE_URL=postgresql+psycopg://dv:dv@postgres:5432/dv`,
   `REDIS_URL=redis://redis:6379/0`, `EXTRACTION_PROVIDER=openai`, `OPENAI_API_KEY`,
   `EXTRACTION_MODEL=gpt-6.1-sol`, and `CREATE_REVIEW_RECORDS=true`, `AUTO_APPLY_MODE=shadow`.
   (`ZOHO_*` there already point to the sandbox.)
2. Start the full engine with the sandbox settings:
   ```cmd
   set ENGINE_ENV_FILE=.env.sandbox
   docker compose up -d --build
   docker compose logs -f worker
   ```
   Check: `curl http://localhost:8000/health/live`
3. Give it a temporary HTTPS address (free, no account needed), in a second window:
   ```cmd
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel --url http://localhost:8000
   ```
   It prints `https://<random>.trycloudflare.com`. Keep the window open while testing;
   the address changes each time you start it.

## 2. Zoho sandbox setup

1. **Org variables** (Setup > Developer Hub > Variables > Create):
   - `dv_engine_url` = the https address (no trailing `/`)
   - `dv_engine_api_key` = the engine's `API_KEY`
2. **Function** (Setup > Developer Hub > Functions > + New Function): name `dv_verify_account`,
   category **Standalone**; arguments `account_id`, `reason`, `user_id`, `user_email` (all String);
   paste `zoho/deluge_dv_verify_account.dg` exactly as is (no comments or apostrophes: the CRM
   editor misreads them); Save. Test it with a sandbox Account id.
3. **Button** (Setup > Customization > Modules and Fields > Accounts > Buttons > + New Button):
   name **Verify Account**, page **In Record**, position **Detail (Standard)** (add the Canvas
   detail position too if your team uses a Canvas record page), profiles as needed.
   - **Recommended - action Function:** create the button function `dv_verify_account_button`
     with argument `account_id` mapped to *Accounts - Account Id*; paste
     `zoho/deluge_dv_verify_account_button.dg`. Zoho shows the returned message. No API setting
     needed; no "Reason" prompt.
   - *Alternative - action Client Script* (`zoho/client_script_verify_button.js`, asks for a
     reason): needs the function's REST API (OAuth2) enabled, otherwise Zoho answers
     "API is inactive for the given custom function".

## 3. Test

1. Open a sandbox Account **without** documents -> Verify Account -> "No documents found..."
   (no engine call).
2. Attach a few real documents (e.g. the BIR 2303, DTI and permit of a sample customer) to a
   sandbox Account -> Verify Account -> "Verification started...".
3. Watch `docker compose logs -f worker`. After 1-3 minutes the Account's
   **Document Verifications** related list shows the new record (DVn) with recommendation,
   alerts, required documents, proposed changes and the page images.
4. Click again while it runs -> "already in progress" (no duplicate job).

## Engine API calls per click

| Done by | Calls |
|---|---|
| Deluge (inside Zoho) | Account, Attachments, Notes (once each) |
| Engine | only file downloads (+ Notes if your Deluge version returns notes without attachment info), then the review record, attachments, alerts |
