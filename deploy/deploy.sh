#!/usr/bin/env sh
# Run on the server from the repository root: pulls the latest main and rebuilds the stack.
# Migrations run automatically when the api container starts.
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
[ -f .env ] || { echo "missing .env (copy .env.example and set POSTGRES_PASSWORD)"; exit 1; }
git pull --ff-only
$DC up -d --build
$DC ps
