PY := backend/.venv/bin/python
LIMIT ?= 200

.PHONY: setup migrate ingest-leaderboard enrich tiers api worker web-dev web-build test lint check

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

check: test lint web-build
