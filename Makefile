.PHONY: help install install-uv test test-cov smoke migrate compose-up compose-down compose-mail seed api worker beat

# Use the backend virtualenv when it exists, so `make test` works without the
# caller having to activate anything. Falls back to python3 for a fresh checkout.
VENV := $(CURDIR)/backend/.venv
PY := $(shell [ -x "$(VENV)/bin/python" ] && echo "$(VENV)/bin/python" || echo python3)

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install backend dependencies into backend/.venv (with dev extras)
	cd backend && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -e ".[dev]"

install-uv: ## Same as install but using uv (faster when available)
	cd backend && uv venv && uv pip install -e ".[dev]"

test: ## Run the backend test suite (sqlite, fake channels, no network)
	cd backend && $(PY) -m pytest

test-cov: ## Run the backend test suite with coverage
	cd backend && $(PY) -m pytest --cov=app --cov-report=term-missing

smoke: ## Drive a running API end to end (start the API first; see README)
	cd backend && $(PY) scripts/smoke_e2e.py --base $${BASE:-http://localhost:8000}

migrate: ## Autogenerate an alembic revision from the current models
	cd backend && $(PY) -m alembic revision --autogenerate -m "$${m:-schema}" && $(PY) -m alembic upgrade head

compose-up: ## Start the full local stack
	docker compose up -d

compose-mail: ## Start the stack with a local mail catcher (http://localhost:8025)
	docker compose --profile mail up -d

compose-down: ## Stop the stack
	docker compose down

seed: ## Re-seed the Acme workspace against the configured database
	cd backend && $(PY) -m app.seed

api: ## Run the API locally without docker
	cd backend && $(PY) -m uvicorn app.main:app --reload

worker: ## Run a celery worker locally
	cd backend && $(PY) -m celery -A app.workers.celery_app:celery_app worker -l info

beat: ## Run the celery beat scheduler locally
	cd backend && $(PY) -m celery -A app.workers.celery_app:celery_app beat -l info
