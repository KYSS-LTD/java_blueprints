# Удобные команды. На Windows без make выполняйте команды из правых колонок напрямую.
.PHONY: dev-backend dev-frontend dev-mock test lint gen-api gen-mocks verify-corpus up doctor submit

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && pnpm dev

dev-mock:
	cd frontend && pnpm dev:mock

test:
	cd backend && uv run pytest
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint && pnpm typecheck

gen-api:
	cd backend && uv run python -m app.export_openapi ../frontend/openapi.json
	cd frontend && pnpm gen:api

gen-mocks:
	cd backend && uv run python ../scripts/gen_mock_fixtures.py

verify-corpus:
	python scripts/verify_corpus.py

up:
	docker compose up --build

doctor:
	python scripts/doctor.py

submit:
	python scripts/submit.py
