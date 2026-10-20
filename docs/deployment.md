# Deployment: one server (recommended start)

```
Zoho CRM (source of truth: accounts, documents, review)
   | Verify Account button  -> HTTPS + API key ->  verification engine (one server)
   |                                               read -> OCR -> AI -> cross-check -> rules
   | <- review record, proposals, alerts, approved updates (OAuth, narrow scopes) --+
```

The engine stores **no permanent document copies** (`KEEP_DOCUMENT_FILES=false`): files are
downloaded from Zoho for each job, processed, page images are attached to the Zoho review record,
then the local files, page images and page OCR text are deleted. The database keeps file
fingerprints, extracted values with evidence, assessments, review links, alerts and the audit
trail. Re-checks download again from Zoho and reuse AI results by fingerprint (no new AI cost).

## Server

- Ubuntu 24.04 VPS in Singapore, 4 vCPU / 8 GB RAM / 80 GB disk (DigitalOcean, Vultr, Hetzner...).
- A DNS name pointing to it, e.g. `dv.yourcompany.com`.
- Firewall: allow only 22 (SSH, your IP), 80 and 443.

## Install

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER   # log out and in again
git clone https://github.com/<you>/doc-verification.git /opt/doc-verification
cd /opt/doc-verification
cp .env.example .env && nano .env
```

In `.env` set: `APP_ENV=prod`, `API_KEY` (long random), `DOMAIN`, `POSTGRES_PASSWORD`,
`DATABASE_URL=postgresql+psycopg://dv:<same password>@postgres:5432/dv`,
`STORAGE_BACKEND=local`, `KEEP_DOCUMENT_FILES=false`, the `ZOHO_*` values (production token),
`EXTRACTION_PROVIDER=openai`, `OPENAI_API_KEY`, `EXTRACTION_MODEL=gpt-6.1-sol`,
and to start: `AUTO_APPLY_MODE=shadow`, `CREATE_REVIEW_RECORDS=true`.

```bash
docker compose -f docker-compose.prod.yml up -d --build
curl https://dv.yourcompany.com/health/live          # {"status":"ok"}
curl -H "X-API-Key: <API_KEY>" https://dv.yourcompany.com/health/zoho
```

Caddy obtains and renews the HTTPS certificate automatically.

## Backups

`deploy/backup.sh` dumps the database daily (14 days kept). Add to cron:
`15 2 * * * /opt/doc-verification/deploy/backup.sh`. Copy `backups/` off the server weekly.

## Updates

```bash
cd /opt/doc-verification && git pull
docker compose -f docker-compose.prod.yml up -d --build   # runs migrations first
```

## Later: Zoho Catalyst

The engine is one Docker image, so it can move to Catalyst AppSail (custom container runtime)
if the company requires hosting inside Zoho. Catalyst *functions* cannot run Tesseract,
LibreOffice or OpenCV; AppSail with this container can.
