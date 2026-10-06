# Test the engine on a new customer

Everything runs locally from the `samples/` folder (git-ignored: real customer data).
On PCs that block compiled libraries (Windows Application Control), run the reading/AI tools
through Docker as shown; `fetch_account.py` runs directly.

## 1. Download the customer's documents from Zoho

```cmd
python scripts/fetch_account.py <account_id>
```

Files land in `samples\<account_id>\files\`. Use any Account ID from the CRM URL.

## 2. Make sure the AI provider is set in `.env`

```
EXTRACTION_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

(or `openai` + `OPENAI_API_KEY` + `EXTRACTION_MODEL`). With `none`, step 3 only reads and
classifies.

## 3. Run extraction and look at the results

```cmd
docker compose run --rm --no-deps api python scripts/try_extraction.py samples/<account_id>/files
```

For each document it prints: type (OCR and AI), page quality, validity/expiry, every field with
the OCR check and confidence, and issues. Full output: `samples\<account_id>\files\extraction_results.json`.

How to read a field line: `tin  601-088-612-00000  [EXACT 0.95]`

| Check | Meaning | What to do |
|---|---|---|
| EXACT | OCR read the same value | Trust it |
| FUZZY | names only, small OCR typo | Usually fine |
| NOT_FOUND | OCR couldn't confirm | Look at the image |
| CONFLICT | OCR read a different number/date | Look at the image: one of them is wrong |

## 4. Add the customer to the gold set (to measure accuracy)

```cmd
docker compose run --rm --no-deps api python scripts/try_extraction.py samples/<account_id>/files --gold-template samples/draft_<account_id>.json
```

Open the draft, compare **every value with the document**, fix mistakes, add values the AI missed,
delete `"_CHECK_ME": true`, then copy the entries into `samples\gold.json`.
Do not skip the checking: an unchecked draft grades the AI against itself.

## 5. Measure accuracy on all labeled customers

```cmd
docker compose run --rm --no-deps api python scripts/evaluate_extraction.py --gold samples/gold.json --files samples
```

Watch two numbers: field accuracy, and **wrong but looked confirmed** (should stay 0).
Aim for 50-100 labeled documents, especially phone photos, before go-live.
