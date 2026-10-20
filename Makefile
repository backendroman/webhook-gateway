.PHONY: up down migrate test test-unit lint fmt

up:
	docker compose up --build -d

down:
	docker compose down

migrate:
	uv run alembic upgrade head

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:
	uv run ruff format .
	uv run ruff check --fix .
