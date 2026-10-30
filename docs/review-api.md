# Review API for the Accounts dashboard ("Document Verification" tab)

Turn on in the engine's `.env`: `ENGINE_REVIEWS=true` (and `CREATE_REVIEW_RECORDS=false` while
the Zoho module is on hold). After every verification the engine creates a review (DV-1, DV-2...)
with the texts, activation checks, proposed changes and copies of the page images.

## Security (important)
- Every call: header `X-API-Key: <engine API key>`.
- Every change: header `X-Reviewer: <name or email of the logged-in dashboard user>`; it is
  stored as the reviewer and shown in Cliq / the Google Sheet.
- **Call the engine from the dashboard's server side** (Cloudflare Worker / Pages Function),
  with the API key stored as a Cloudflare secret. Never put the API key in browser code.
  (Only if the browser must call directly: set `DASHBOARD_ORIGINS=https://<dashboard>` for CORS -
  not recommended.)
- The dashboard decides who may approve (its own login).

## Statuses
`Pending Review` -> (start / any row decision) -> `In Review` -> approve -> `Applied` | `Conflict`
| `Failed`; or -> `Rejected` / `Waiting for Documents`. `No Action` = nothing to decide.
Closed reviews cannot change. A new verification of the Account creates a new review.

## Endpoints (base: https://dv.rareglobalfood.com)
| Method | Path | Body | Use |
| --- | --- | --- | --- |
| GET | `/v1/reviews?status=open` | | Tab list: open reviews (`status=` also exact, `account_id=`, `limit`, `offset`) |
| GET | `/v1/reviews/{id}` | | Review screen: summary texts, activation checks, rows, pages |
| GET | `/v1/reviews/{id}/pages/{n}` | | Page image (JPEG) to show next to the values |
| POST | `/v1/reviews/{id}/start` | | Reviewer opens it (status In Review) |
| PUT | `/v1/reviews/{id}/rows/{row_id}` | `{"decision": "Approve"\|"Edit"\|"Reject"\|"Pending", "value": "...", "comment": "..."}` | Decision per row; `value` required for Edit |
| POST | `/v1/reviews/{id}/approve` | `{"values_compared": true, "activate_account": false, "notes": "..."}` | Writes Approve/Edit rows to the Zoho Account (conflict check), optional activation |
| POST | `/v1/reviews/{id}/reject` | `{"notes": "reason"}` | Close without writing |
| POST | `/v1/reviews/{id}/request-documents` | `{"notes": "which documents"}` | Close, waiting for documents |

Also existing: `POST /v1/jobs` (start a verification), `GET /v1/jobs/{id}` (job status).

### List item (`GET /v1/reviews`)
```json
{"id": "8f0c...", "number": "DV-12", "account_id": "5906238000035917292",
 "account_name": "Auro Chocolate Cafe Inc. (Makati) - Commissary", "customer_number": "RGFC-01998",
 "status": "Pending Review", "recommendation": "MANUAL_REVIEW", "checks_met": "4/5",
 "rows_to_decide": 3, "requested_by": "rep@rareglobalfood.com", "reviewer": null,
 "created_at": "2026-11-02T03:15:00+00:00", "closed_at": null}
```
### Review (`GET /v1/reviews/{id}`) adds
- `summary.recommendation_reasons`, `summary.alerts`, `summary.required_documents`: ready-to-show
  text (one item per line)
- `summary.activation_checks`: `[{"key", "label", "status": "PASS|AFTER_UPDATE|FAIL", "detail"}]`
- `rows`: `[{"id", "zoho_field", "field_label", "current_value", "proposed_value", "action",
  "confidence", "ocr_check", "evidence", "auto_applied", "decision", "edited_value", "comment",
  "outcome", "outcome_detail"}]` - show rows whose `action` is FILL / CORRECT / REVIEW_CONFLICT /
  HOLD / DIFFERS with a decision control; MATCH rows are information.
- `pages`: `[{"n": 1, "file": "2303.jpg", "page": 1}]` (images kept 30 days after closing)

### Approve result
`{"status": "Applied|Conflict|Failed", "activated": true|false|null, "rows": [{"id", "field",
"outcome": "APPLIED|CONFLICT|FAILED|UNSUPPORTED|NO_VALUE|NOT_APPROVED", "detail"}], "review": {...}}`

### Errors
401 wrong API key - 404 unknown review / page - 409 not allowed (closed review, Edit without
value, approve without `values_compared`, empty notes) with `detail` text to show - 422 missing
`X-Reviewer` or bad body.

## Cloudflare Worker proxy (sketch)
```js
// env.DV_ENGINE_URL = "https://dv.rareglobalfood.com", env.DV_API_KEY = secret
export async function onRequest({ request, env, data }) {      // Pages Function: /api/dv/*
  const user = data.user;                                       // from your dashboard login
  if (!user) return new Response("Unauthorized", { status: 401 });
  const url = new URL(request.url);
  const target = env.DV_ENGINE_URL + url.pathname.replace(/^\/api\/dv/, "") + url.search;
  return fetch(target, {
    method: request.method,
    headers: { "X-API-Key": env.DV_API_KEY, "X-Reviewer": user.email,
               "Content-Type": "application/json" },
    body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
  });
}
```
