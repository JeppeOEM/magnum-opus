.PHONY: test test-all install

## Install dependencies into .venv
install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

## Run tests
test:
	.venv/bin/pytest tests/ --tb=short -q

test-all: test
