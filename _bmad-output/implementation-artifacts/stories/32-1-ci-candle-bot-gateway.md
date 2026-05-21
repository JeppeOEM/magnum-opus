---
id: 32-1
title: CI for candle-service, bot-service, and gateway
epic: 32
status: done
---

# Story 32-1: CI for candle-service, bot-service, and gateway

## Context

`.github/workflows/ci.yml` only tests the aggregator (Go). Candle-service, bot-service, and gateway are all untested in CI — regressions ship silently. This story extends the CI workflow with separate jobs for each service, triggered on path-scoped pushes.

## What to build

### `.github/workflows/ci.yml` — add three new jobs

**Candle-service job** (same pattern as aggregator test job):
```yaml
  candle-test:
    name: Candle-service L1 + L2
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: candle-service
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: "1.24"
          cache-dependency-path: candle-service/go.sum
      - run: go mod download
      - name: L1 tests
        run: make test-l1
      - name: L2 tests
        run: make test-l2
```

**Bot-service job** (Python):
```yaml
  bot-test:
    name: Bot-service L1
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: bot-service
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: bot-service/requirements*.txt
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - name: L1 unit tests
        run: pytest -m "l1" -x --tb=short
```

**Gateway job** (Go):
```yaml
  gateway-test:
    name: Gateway tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: gateway
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: "1.24"
          cache-dependency-path: gateway/go.sum
      - run: go mod download
      - name: Tests
        run: go test ./...
```

### Update `on.push.paths` and `on.pull_request.paths`

Each job needs to trigger only when its source changes. Add path filters to `on`:

```yaml
on:
  push:
    branches: ["**"]
    paths:
      - "aggregator/**"
      - "candle-service/**"
      - "bot-service/**"
      - "gateway/**"
      - ".github/workflows/ci.yml"
  pull_request:
    branches: ["**"]
    paths:
      - "aggregator/**"
      - "candle-service/**"
      - "bot-service/**"
      - "gateway/**"
      - ".github/workflows/ci.yml"
```

Use `if` conditions on each job to gate by changed paths (GitHub Actions does not natively filter per-job by path — run all jobs whenever any path matches; this is acceptable for the scale of this repo).

## Acceptance Criteria

1. `candle-test` job exists and runs `make test-l1` and `make test-l2` from `candle-service/`.
2. `bot-test` job exists and runs `pytest -m "l1" -x` from `bot-service/` with Python 3.12.
3. `gateway-test` job exists and runs `go test ./...` from `gateway/`.
4. `on.push.paths` includes all four service directories and `ci.yml`.
5. No new GitHub Actions syntax errors (validate with `actionlint` if available; else inspect manually).
6. Bot-service job installs deps from `requirements.txt` and `requirements-dev.txt`.

## Dev Notes

- Bot-service L2 tests need mocked Redis — but `pytest -m "l2"` uses mocked clients per pyproject.toml markers. L2 is safe in CI without a real Redis instance.
- Bot-service `requirements-dev.txt` exists at `bot-service/requirements-dev.txt`. Verify before assuming.
- Gateway tests run with `go test ./...` — `hub_test.go` and `codec_test.go` are pure unit tests, no external deps.
- Candle-service L2 uses mock infra (`-tags l2`); no real Redis/QuestDB needed.

### Review Findings

- [x] [Review][Patch] Added `--strict-markers` to pytest — prevents unmarked tests from silently running in CI [`ci.yml`]
- [x] [Review][Patch] Added `-race -count=1` to gateway `go test` — detects concurrency bugs, prevents cache [`ci.yml`]
- [x] [Review][Defer] Candle-service L2 missing timeout in Makefile — pre-existing; add `-timeout 2m` to `test-l2` target in a follow-up
- [x] [Review][Defer] Bot-service mypy step omitted — add after mypy audit confirms existing code passes strict mode

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### File List

- `.github/workflows/ci.yml`
