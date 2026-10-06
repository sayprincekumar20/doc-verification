# Zoho CRM API access (OAuth) setup

The engine authenticates with Zoho using OAuth 2.0 and a **Self Client**.

| Token | Purpose | Lifetime |
|---|---|---|
| Grant code | One-time code from the API Console, exchanged for a refresh token | A few minutes |
| Refresh token | Stored in `.env` as `ZOHO_REFRESH_TOKEN`; the engine uses it to get access tokens | Does not expire (until revoked) |
| Access token | Sent on every API call as `Authorization: Zoho-oauthtoken <token>` | 1 hour |

The engine refreshes the access token automatically, caches it in Redis, and refreshes at most about
once per hour. Zoho allows only about 10 refreshes per 10 minutes per refresh token, so never refresh
per request.

Data center: US (`https://accounts.zoho.com`, API domain `https://www.zohoapis.com`).

## Who should create the Self Client

The engine acts with the permissions of the Zoho user who creates the Self Client. Use an
**administrator or a dedicated integration user**, not a sales rep. Otherwise some Accounts or
attachments may be hidden from the engine.

## One-time setup

1. Print the scopes:

   ```bash
   python scripts/zoho_auth.py scopes
   ```

   Phase 1 is read-only:
   `ZohoCRM.modules.accounts.READ, ZohoCRM.modules.notes.READ, ZohoCRM.modules.attachments.READ,
   ZohoCRM.settings.fields.READ, ZohoCRM.users.READ, ZohoCRM.Files.READ`.
   Phase 4 will add write scopes (updating Accounts, creating review records).

2. In https://api-console.zoho.com (logged in as the integration user): create a **Self Client**,
   copy its **Client ID** and **Client Secret** into `.env` (`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`).

3. In the Self Client, open **Generate Code**, paste the scopes, choose the longest duration,
   add a description, and create the code. Copy it.

4. Immediately exchange it (input is hidden; the refresh token is written to `.env`):

   ```bash
   python scripts/zoho_auth.py exchange-code
   ```

5. Verify everything, including Notes/Attachments access for a real account:

   ```bash
   python scripts/zoho_auth.py test --account-id <account_id>
   ```

   Every line should say `OK`. A `missing scope` line names the scope to add: generate a new grant
   code with the full scope list and repeat step 4.

6. With the engine running, `GET /health/zoho` (with the `X-API-Key` header) confirms the deployed
   engine can reach Zoho and shows which Zoho user it acts as.

## Download a client's data for testing

```bash
python scripts/fetch_account.py <account_id>
```

Saves `samples/<account_id>/` with `account.json`, `notes.json`, `attachments.json`,
`files/` (every document) and `manifest.json` (source, name, type, size, SHA-256, status).
`samples/` is git-ignored because it holds real customer data. Never commit it.

## Postman

Import both files from `postman/`:

- `doc-verification-zoho.postman_collection.json`
- `zoho-crm.postman_environment.json`

Select the **Zoho CRM (Doc Verification)** environment, fill `zoho_client_id`,
`zoho_client_secret`, and either `zoho_refresh_token` (if you already have one) or
`zoho_grant_code` + run **1. Setup / Exchange grant code**. After that every request gets a fresh
access token automatically from the collection's pre-request script, so you never paste tokens.
Keep secrets in the environment only (type "secret"); don't save them into the collection or share
an exported environment that contains them.

## If a token is exposed

```bash
python scripts/zoho_auth.py revoke
```

revokes the refresh token at Zoho and clears it from `.env`. Then repeat steps 3–5. Also rotate the
Client Secret in the API Console if it was exposed.

## Common errors

| Error | Meaning | Fix |
|---|---|---|
| `invalid_code` | Grant code expired or already used | Generate a new code, exchange it right away |
| `invalid_client` | Wrong client id/secret or wrong accounts URL | Check `.env`; US uses `accounts.zoho.com` |
| `OAUTH_SCOPE_MISMATCH` (401) | Token lacks a scope | New grant code with the full scope list |
| `INVALID_TOKEN` (401) | Access token expired | Handled automatically (one refresh + retry) |
| Token refresh blocked ~10 min | Too many refreshes | Wait; tokens are cached to avoid this |
| `NO_PERMISSION` (403) | The integration user can't see that record/module | Use an admin/integration user |

## Windows: "An Application Control policy has blocked this file"

Some company PCs (Smart App Control / WDAC) block compiled Python libraries such as SQLAlchemy's
DLLs. The setup scripts (`zoho_auth.py`, `fetch_account.py`) don't use any database library, so they
run anyway. To run the full engine or the test suite on such a PC, use Docker Desktop or WSL
(Linux), which is also how production runs, or ask IT to allow the project's `.venv` folder.
