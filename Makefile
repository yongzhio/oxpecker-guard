# Oxpecker Guard development tasks.
#
# Test tiers (per level-set §8.7):
#   `make test`       — unit + integration; no live model needed; runs in CI
#   `make test-live`  — live-model tests; requires LM Studio/Ollama on the host
#
# Convenience targets:
#   `make lint`       — ruff lint
#   `make format`     — ruff format (writes)
#   `make typecheck`  — mypy
#   `make check`      — lint + format-check + typecheck + tests (CI-equivalent)

.PHONY: install test test-live lint format format-check typecheck check clean

install:
	pip install -e ".[dev]"

test:
	pytest -v --tb=short

test-live:
	@echo "Live-model tests are not implemented in v0."
	@echo "They will be added with the first demo that requires a model."
	@exit 1

lint:
	ruff check opg tests

format:
	ruff format opg tests

format-check:
	ruff format --check opg tests

typecheck:
	mypy opg

check: lint format-check typecheck test

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
