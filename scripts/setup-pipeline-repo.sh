#!/usr/bin/env bash
# setup-pipeline-repo.sh — extract magnum-opus-pipeline from the monorepo.
#
# This script creates a new git repo containing ONLY the pipeline services
# (aggregator, candle-service, monitoring, scripts, docker-compose.pipeline.yml)
# with full commit history preserved.
#
# Prerequisites:
#   pip install git-filter-repo      (https://github.com/newren/git-filter-repo)
#   GitHub repo 'magnum-opus-pipeline' created (private, empty, no README)
#
# Usage:
#   bash scripts/setup-pipeline-repo.sh
#   # Then follow the "Next steps" printed at the end.
#
# What this does NOT do:
#   - Delete anything from the monorepo (the monorepo stays intact)
#   - Push to GitHub (you do that manually after reviewing the extracted repo)

set -euo pipefail

MONOREPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE_REPO_DIR="${PIPELINE_REPO_DIR:-/tmp/magnum-opus-pipeline}"
GITHUB_USER="${GITHUB_USER:-JeppeOEM}"

log() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
command -v git-filter-repo >/dev/null 2>&1 || \
  fail "git-filter-repo not found. Install: pip install git-filter-repo"

[ -d "$MONOREPO_DIR/.git" ] || \
  fail "$MONOREPO_DIR is not a git repository"

if [ -d "$PIPELINE_REPO_DIR" ]; then
  log "WARNING: $PIPELINE_REPO_DIR already exists — removing it"
  rm -rf "$PIPELINE_REPO_DIR"
fi

# ── Clone the monorepo ────────────────────────────────────────────────────────
log "Cloning monorepo to $PIPELINE_REPO_DIR ..."
git clone "$MONOREPO_DIR" "$PIPELINE_REPO_DIR"
cd "$PIPELINE_REPO_DIR"

# ── Filter to pipeline paths only ─────────────────────────────────────────────
# Keep only: aggregator/, candle-service/, monitoring/, scripts/,
#            docker-compose.pipeline.yml, config.yaml, .env.example,
#            VERSION, Makefile, .github/
log "Filtering history to pipeline paths ..."
# Note: --path scripts/ is included but setup-*.sh scripts are excluded afterwards.
# git filter-repo --path includes the path but we cannot --path-exclude in the same pass
# when using inclusion mode, so we remove the setup scripts in a follow-up commit.
git filter-repo \
  --path aggregator/ \
  --path candle-service/ \
  --path monitoring/ \
  --path scripts/ \
  --path docker-compose.pipeline.yml \
  --path config.yaml \
  --path .env.example \
  --path VERSION \
  --path Makefile \
  --path .github/ \
  --force

# Remove setup scripts that belong to monorepo orchestration, not the pipeline repo.
for f in scripts/setup-pipeline-repo.sh scripts/setup-trading-repo.sh; do
  if [ -f "$f" ]; then
    git rm --cached "$f" 2>/dev/null || true
    rm -f "$f"
  fi
done
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -m "chore: remove monorepo-only setup scripts from pipeline repo"
fi

# ── Rename docker-compose.pipeline.yml → docker-compose.yml ──────────────────
# In the pipeline repo, this IS the docker-compose.yml.
log "Renaming docker-compose.pipeline.yml to docker-compose.yml ..."
if [ -f docker-compose.pipeline.yml ]; then
  git mv docker-compose.pipeline.yml docker-compose.yml
  git commit -m "chore: rename docker-compose.pipeline.yml → docker-compose.yml for standalone repo

This file is the pipeline-repo-specific compose. In the monorepo it was named
docker-compose.pipeline.yml to avoid conflicting with the root docker-compose.yml."
fi

# ── Create pipeline-specific Makefile ─────────────────────────────────────────
log "Creating pipeline Makefile ..."
cat > Makefile << 'MAKEFILE_EOF'
SHELL      := /bin/bash
VERSION    := $(shell cat VERSION 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs deploy build test release validate-contract

## Start aggregator + candle-blue + redis + questdb
up:
	docker compose --profile candle-blue up -d --build
	@echo ""
	@echo "  aggregator   http://localhost:8080/health"
	@echo "  candle-blue  http://localhost:8081/health"
	@echo "  questdb      http://localhost:9000"
	@echo "  redis        localhost:6379"
	@echo ""
	@echo "  Consumer repos connect to: REDIS_URL=redis://localhost:6379"
	@echo "                             QUESTDB_URL=http://localhost:9000"

## Start with full monitoring stack
up-monitoring:
	docker compose --profile candle-blue --profile monitoring up -d --build

## Stop all pipeline services
down:
	docker compose --profile candle-blue --profile candle-green --profile monitoring down

## Tail aggregator + active candle-service logs
logs:
	docker compose --profile candle-blue logs -f aggregator candle-blue

## Deploy candle-service (zero-downtime blue-green swap)
## Usage: make deploy SLOT=green  (deploys green, old=blue)
SLOT ?= green
OLD_SLOT = $(if $(filter green,$(SLOT)),blue,green)
deploy:
	bash scripts/deploy-candle.sh $(SLOT) $(OLD_SLOT)

## Build Docker images without starting
build:
	docker compose --profile candle-blue build aggregator candle-blue candle-green

## Run all Go tests (unit + L1 + L2)
test:
	@echo "=== aggregator ==="
	cd aggregator && go test ./...
	@echo "=== candle-service ==="
	cd candle-service && go test ./...

## Validate pipeline ↔ trading contract (requires running pipeline)
validate-contract:
	python3 scripts/validate-contract.py

## Tag and release a new version
## Usage: make release VERSION=1.1.0
release:
	@[ -n "$(VERSION)" ] || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	@echo "$(VERSION)" > VERSION
	git add VERSION
	git commit -m "chore: release pipeline-v$(VERSION)"
	git tag "pipeline-v$(VERSION)"
	@echo ""
	@echo "  ✓ Tagged pipeline-v$(VERSION)"
	@echo "  Run: git push origin main --tags"
MAKEFILE_EOF

git add Makefile
git commit -m "chore: pipeline-specific Makefile with up/down/deploy/test/release targets"

# ── Update .github/workflows/ci.yml to pipeline-only paths ───────────────────
log "Updating CI workflow to pipeline-only paths ..."
if [ -f .github/workflows/ci.yml ]; then
  cat > .github/workflows/ci.yml << 'CI_EOF'
name: CI

on:
  push:
    branches: ["**"]
    paths:
      - "aggregator/**"
      - "candle-service/**"
      - "scripts/**"
      - ".github/workflows/ci.yml"
  pull_request:
    branches: ["**"]

jobs:
  aggregator:
    name: Aggregator — build & test
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: aggregator
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache-dependency-path: go.sum
      - run: go mod download
      - run: make check-deps
      - run: make test-l1
      - run: make test-l2

  candle-service:
    name: Candle service — build & test
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: candle-service
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache-dependency-path: go.sum
      - run: go mod download
      - run: make test-l1
      - run: make test-l2

  validate-contract:
    name: Contract validation (placeholder)
    runs-on: ubuntu-latest
    # Enable once self-hosted runner with Redis + QuestDB is available.
    # Run locally: python3 scripts/validate-contract.py
    if: false
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install redis requests
      - run: python3 scripts/validate-contract.py
CI_EOF
  git add .github/workflows/ci.yml
  git commit -m "ci: scope CI to pipeline paths only (aggregator, candle-service, scripts)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Pipeline repo extracted to: $PIPELINE_REPO_DIR"
log ""
log "Next steps:"
log "  1. Review the extracted repo:"
log "       cd $PIPELINE_REPO_DIR && git log --oneline | head -20"
log ""
log "  2. Create GitHub repo (empty, private):"
log "       gh repo create ${GITHUB_USER}/magnum-opus-pipeline --private"
log ""
log "  3. Push to GitHub:"
log "       cd $PIPELINE_REPO_DIR"
log "       git remote add origin git@github.com:${GITHUB_USER}/magnum-opus-pipeline.git"
log "       git push -u origin main"
log "       git push --tags"
log ""
log "  4. Verify CI passes on GitHub."
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
