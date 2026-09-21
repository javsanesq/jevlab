.PHONY: install dev test lint

install:
	uv run python scripts/install.py $(if $(COACH),--coach $(COACH))

dev:
	uv run --all-extras jev

test:
	uv run --all-extras pytest -q

lint:
	uv run --all-extras ruff check .
	uv run --all-extras ruff format --check .
	uv run --all-extras pyright
