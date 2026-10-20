#!/bin/sh
# Daily database backup (results + audit trail; no documents are stored).
# Cron (as the deploy user):  15 2 * * *  /opt/doc-verification/deploy/backup.sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y-%m-%d)
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U dv dv | gzip > "backups/dv-$STAMP.sql.gz"
find backups -name 'dv-*.sql.gz' -mtime +14 -delete   # keep 14 days
