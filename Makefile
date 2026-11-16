PY := backend/.venv/bin/python
LIMIT ?= 200

.PHONY: setup migrate ingest-leaderboard enrich tiers market swings fills binance positions reconcile api worker web-dev web-build test lint shellcheck check

setup: ## create venv, install backend + frontend deps, migrate
	test -d backend/.venv || python3.11 -m venv backend/.venv
	$(PY) -m pip install -q -r backend/requirements.txt pytest
	cd frontend && npm ci
	$(MAKE) migrate

migrate:
	cd backend && .venv/bin/alembic upgrade head

ingest-leaderboard: ## fetch the official leaderboard (conditional GET) and ingest it
	cd backend && .venv/bin/python -m app.cli ingest-leaderboard

enrich: ## enrich LIMIT tier-B wallets with portfolio + positions, within the API budget
	cd backend && .venv/bin/python -m app.cli enrich --limit $(LIMIT)

tiers:
	cd backend && .venv/bin/python -m app.cli tiers

market: ## record candles for the reference coins
	cd backend && .venv/bin/python -m app.cli market

swings: ## recompute swing legs, per-wallet leg returns and fill candidates
	cd backend && .venv/bin/python -m app.cli swings

binance: ## import Binance USDT-M klines (only missing files)
	cd backend && .venv/bin/python -m app.cli binance

positions: ## rebuild position cycles for wallets with new fills
	cd backend && .venv/bin/python -m app.cli positions

reconcile: ## compare rebuilt positions with live clearinghouse positions
	cd backend && .venv/bin/python -m app.cli reconcile --limit 10

fills: ## archive fills for queued swing candidates
	cd backend && .venv/bin/python -m app.cli fills --limit $(LIMIT)

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	cd backend && .venv/bin/python -m app.worker

web-dev:
	cd frontend && npm run dev

web-build:
	cd frontend && npm run build

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd frontend && npm run lint && npx tsc --noEmit

shellcheck:
	shellcheck -x install.sh start.sh scripts/deploy-common.sh && shellcheck -s sh deploy/alphascope-cli.sh

check: test lint shellcheck web-build
