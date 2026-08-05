.PHONY: dev up down api web install migrate test lint build check

# One command to get everything running locally.
dev: up
	@echo "Postgres up. Run 'make api' and 'make web' in separate shells."

install:
	cd backend && uv venv --python 3.12 && uv pip install -e ".[dev]"
	cd frontend && pnpm install

up:
	docker compose up -d
	@until docker compose exec -T db pg_isready -U pixii -d pixii_intelligence >/dev/null 2>&1; do sleep 1; done
	@echo "database ready on localhost:5433"

down:
	docker compose down

migrate: up
	cd backend && .venv/bin/alembic upgrade head

api: migrate
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web:
	cd frontend && pnpm dev

# Both suites. `check` ran backend pytest only for the whole of V1 and V2, so every frontend
# test written in those runs was outside the gate: an agent that ran `make check` and reported
# green had no evidence at all about the frontend, and said so honestly only because it happened
# to run vitest separately. Two of the three US-00x frontend slices in this run reported exactly
# that discrepancy unprompted, which is how it was found.
test:
	cd backend && .venv/bin/pytest -q
	cd frontend && pnpm test

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/mypy app
	cd frontend && pnpm lint && pnpm exec tsc --noEmit

build:
	cd frontend && pnpm build

check: lint test build
