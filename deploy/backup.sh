#!/usr/bin/env sh
# Nightly Postgres dump, keeps 14 days. Example cron: 30 3 * * * /srv/alphascope/deploy/backup.sh
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
mkdir -p backups
$DC exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/alphascope-$(date -u +%Y%m%dT%H%M%SZ).dump"
find backups -name 'alphascope-*.dump' -mtime +14 -delete
