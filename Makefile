.PHONY: install dev test lint

install:
	uv run --no-project --python '>=3.12' python scripts/install.py $(if $(COACH),--coach $(COACH))

dev:
	uv run --all-extras jevlab

test:
	uv run --all-extras pytest -q -n auto

lint:
	uv run --all-extras ruff check .
	uv run --all-extras ruff format --check .
	uv run --all-extras pyright
