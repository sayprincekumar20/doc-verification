# Zoho CRM API calls used by the engine

All calls use `https://www.zohoapis.com/crm/v8` with header `Authorization: Zoho-oauthtoken <token>`.
These requests follow the Zoho CRM v8 API conventions.

| Step | Request | Used for |
|---|---|---|
| 1 | `GET /Accounts/{account_id}` | Account snapshot (expected values), fileupload fields, customer number check |
| 2 | `GET /Accounts/{account_id}/Notes?fields=id,Parent_Id,Owner,Created_By,Modified_By,Created_Time,Modified_Time,Note_Title,Note_Content,$attachments` | Files attached to notes (`$attachments`, often `null`) |
| 3 | `GET /Accounts/{account_id}/Attachments?fields=id,File_Name,File_Size,Created_Time,Modified_Time,Owner,Created_By,Modified_By,Parent_Id,Attachment_Type` | List of uploaded documents |
| 4 | `GET /Accounts/{account_id}/Attachments/{attachment_id}` | Download each document (binary) |
| 5 | `GET /Notes/{note_id}/Attachments/{attachment_id}` | Download note files (only when `$attachments` is not null) |
| 6 | `GET /files?id={File_Id__s}` | Download files in fileupload fields (only when the field is not null) |

Lists are paged with `per_page=200` and follow `info.more_records` / `next_page_token`.
A `204 No Content` reply means an empty list.

Example: step 1 returns the Account, step 2 returns one note with
`"$attachments": null`, step 3 returns 3 JPGs, step 4 runs three times. Result: 3 documents stored.
