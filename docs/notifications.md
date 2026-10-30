# Notifications: Zoho Cliq channel + Google Sheets log

The engine announces in a Cliq channel and logs in one Google Sheet:

| Event | Cliq | Sheet tab |
| --- | --- | --- |
| Verification done (button / Run) | ✅ result, checks, missing items, automatic updates / activation | Verifications (+ Field Updates, Activations) |
| Review approved (Applied / Conflict / Failed) | 👍 who approved, fields written, activation | Field Updates (+ Activations) |
| Document expiring in 30 / 7 / 0 days or expired | ⚠️ reminder to collect the renewal | Document Expiry (Status Open) |
| Alerted document renewed | ♻️ renewed, new expiry date | Document Expiry (Status Renewed) |

Each message is sent once (table `notifications`). Reviews are checked every
REVIEW_APPLY_INTERVAL_SECONDS; the expiry check runs once a day after EXPIRY_CHECK_HOUR (Manila).
Before an expiry reminder, the engine looks at the Account's attachments: if files were added
after the last verification, it re-verifies the Account first (only new files go to the AI) and
reminds only if the document is still expiring. Reviews older than 3 days when the feature is
first switched on are recorded silently (no flood).

## Zoho Cliq
1. Channel: unique name (e.g. `accountactivation`); its API endpoint is shown in the channel's
   API parameters: `https://cliq.zoho.com/company/<id>/api/v2/channelsbyname/<name>/message`
2. Cliq > Bots & Tools > Webhook Tokens > Generate token.
3. `.env`: `CLIQ_MESSAGE_URL=<endpoint>` and `CLIQ_WEBHOOK_TOKEN=<token>`.

## Google Sheet (service account, nobody has to log in)
1. Create an empty Google Sheet in the Drive folder (e.g. "Account Activation Log"); copy its id
   from the address: `https://docs.google.com/spreadsheets/d/<THIS PART>/edit`.
2. console.cloud.google.com > create/select a project > APIs & Services > Library > enable
   **Google Sheets API**.
3. IAM & Admin > Service Accounts > Create (name `dv-engine`, no roles needed) > open it > Keys >
   Add key > JSON: a key file downloads. Keep it private.
4. In the Sheet: Share > add the service account's email (`dv-engine@<project>.iam.gserviceaccount.com`)
   as **Editor**.
5. `.env`: `GOOGLE_SHEET_ID=<id>` and `GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-sa.json`
   (on the server copy the key file there; locally any path), or paste the whole key into
   `GOOGLE_SERVICE_ACCOUNT_JSON`.
The engine creates the four tabs with header rows on first use.

## Test
`python scripts/test_notifications.py --env .env.sandbox` posts one test message and one test row.
