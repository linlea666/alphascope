PY := backend/.venv/bin/python

.PHONY: setup migrate api worker web-dev web-build test lint shellcheck check

setup: ## create venv, install deps, create the dev database, migrate and seed
	test -d backend/.venv || python3.11 -m venv backend/.venv
	$(PY) -m pip install -q -r backend/requirements.txt pytest
	cd frontend && npm ci
	createdb newsroom 2>/dev/null || true
	createdb newsroom_test 2>/dev/null || true
	$(MAKE) migrate

migrate:
	cd backend && .venv/bin/alembic upgrade head && .venv/bin/python -m nr.cli seed

api:
	cd backend && .venv/bin/uvicorn nr.main:app --reload --port 8620

worker:
	cd backend && .venv/bin/python -m nr.worker

web-dev:
	cd frontend && NEWSROOM_API_BASE=http://127.0.0.1:8620 npx next dev -p 3300

web-build:
	cd frontend && npm run build

test:
	cd backend && .venv/bin/python -m pytest -q

lint:
	cd frontend && npm run lint && npx tsc --noEmit

shellcheck:
	shellcheck -x start.sh scripts/nr-deploy-common.sh && shellcheck -s sh deploy/newsroom-cli.sh

check: test lint shellcheck web-build
