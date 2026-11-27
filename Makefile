# Levigate developer commands. Run `make help` for the list.
.PHONY: help setup demo api web test lint e2e up

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-8s %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm ci

demo: ## Reset the demo environment (synthetic warehouse + app DB) — restart the API afterwards
	cd backend && uv run python -m levigate.demo.seed

api: ## Run the API on :8000 with reload
	cd backend && uv run uvicorn levigate.main:app --reload --port 8000

web: ## Run the web app on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

test: ## Backend and frontend tests
	cd backend && uv run pytest -q
	cd frontend && npm test

lint: ## Lint, format check, types and security analysis
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy levigate && uv run bandit -q -r levigate -c pyproject.toml --severity-level high
	cd frontend && npm run lint && npm run typecheck

e2e: ## Smoke-test the demo script (needs `make demo`, then `make api` and `make web` running)
	cd frontend && npm run e2e

up: ## Full stack in Docker on http://localhost:8080 (PostgreSQL, API, web)
	docker compose up --build
