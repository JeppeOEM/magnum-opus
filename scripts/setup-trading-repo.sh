#!/usr/bin/env bash
# setup-trading-repo.sh — create magnum-opus-trading consumer repo.
#
# This script creates a new git repo for trading consumers (dashboard, bots, ML).
# The dashboard is extracted from the monorepo with full history.
# Skeleton directories for bots and ml are created empty.
#
# Prerequisites:
#   pip install git-filter-repo
#   GitHub repo 'magnum-opus-trading' created (private, empty, no README)
#
# Usage:
#   bash scripts/setup-trading-repo.sh
#
# The pipeline repo (magnum-opus-pipeline) must be running before this repo
# can connect to Redis and QuestDB.

set -euo pipefail

MONOREPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TRADING_REPO_DIR="${TRADING_REPO_DIR:-/tmp/magnum-opus-trading}"
GITHUB_USER="${GITHUB_USER:-JeppeOEM}"

log() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
command -v git-filter-repo >/dev/null 2>&1 || \
  fail "git-filter-repo not found. Install: pip install git-filter-repo"

[ -d "$MONOREPO_DIR/.git" ] || \
  fail "$MONOREPO_DIR is not a git repository"

if [ -d "$TRADING_REPO_DIR" ]; then
  log "WARNING: $TRADING_REPO_DIR already exists — removing it"
  rm -rf "$TRADING_REPO_DIR"
fi

# ── Clone and filter ──────────────────────────────────────────────────────────
log "Cloning monorepo to $TRADING_REPO_DIR ..."
git clone "$MONOREPO_DIR" "$TRADING_REPO_DIR"
cd "$TRADING_REPO_DIR"

log "Filtering history to consumer paths ..."
git filter-repo \
  --path dashboard/ \
  --force

# ── Create skeleton structure ─────────────────────────────────────────────────
log "Creating consumer repo skeleton ..."
mkdir -p bots ml

touch bots/.gitkeep
touch ml/.gitkeep

cat > .env.example << 'ENV_EOF'
# .env.example — magnum-opus-trading
# Copy to .env and adjust for your environment.
#   cp .env.example .env
#
# Prerequisites: magnum-opus-pipeline must be running.
# The pipeline exposes Redis on :6379 and QuestDB HTTP on :9000.

# ── Pipeline connection ────────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379
QUESTDB_URL=http://localhost:9000       # used by validate-contract.py and consumers

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PORT=8050

# ── Bot service (future) ──────────────────────────────────────────────────────
# BOT_SERVICE_URL=http://localhost:8090

# ── ML service (future) ───────────────────────────────────────────────────────
# ML_SERVICE_URL=http://localhost:8000
ENV_EOF

cat > docker-compose.yml << 'COMPOSE_EOF'
# magnum-opus-trading docker-compose.yml
# Starts consumer services only. Assumes magnum-opus-pipeline is running.
#
# network_mode: host — containers connect to pipeline's localhost ports directly.
# No Redis or QuestDB here — those are owned by the pipeline repo.
#
# Usage:
#   cp .env.example .env
#   docker compose up -d

services:
  dashboard:
    build: ./dashboard
    network_mode: host   # connects to localhost:6379 (Redis) and localhost:9000 (QuestDB)
    environment:
      REDIS_URL: ${REDIS_URL:-redis://localhost:6379}
      QUESTDB_HTTP_ADDR: ${QUESTDB_URL:-http://localhost:9000}
      ML_SERVICE_URL: ${ML_SERVICE_URL:-http://localhost:8000}
      BOT_SERVICE_URL: ${BOT_SERVICE_URL:-http://localhost:8090}
    ports:
      - "${DASHBOARD_PORT:-8050}:8050"
    deploy:
      resources:
        limits:
          memory: 512m
    restart: unless-stopped
COMPOSE_EOF

cat > Makefile << 'MAKEFILE_EOF'
SHELL := /bin/bash

.PHONY: up down logs dashboard test validate-contract

## Start all consumer services (dashboard, etc.)
## Requires: magnum-opus-pipeline running (Redis:6379, QuestDB:9000)
up:
	docker compose up -d --build
	@echo ""
	@echo "  dashboard    http://localhost:8050"
	@echo ""
	@echo "  Connecting to pipeline at:"
	@echo "    REDIS_URL=${REDIS_URL:-redis://localhost:6379}"
	@echo "    QUESTDB_URL=${QUESTDB_HTTP_ADDR:-http://localhost:9000}"

## Stop all consumer services
down:
	docker compose down

## Tail dashboard logs
logs:
	docker compose logs -f dashboard

## Start or restart dashboard only
dashboard:
	docker compose up -d --build dashboard

## Run dashboard Python syntax check
test:
	cd dashboard && python3 -m py_compile app.py callbacks.py callbacks_bots.py \
	  callbacks_chart.py callbacks_ml.py data.py bot_data.py backtest_data.py
	@echo "Syntax OK"

## Validate pipeline contract (requires pipeline running)
validate-contract:
	python3 scripts/validate-contract.py
MAKEFILE_EOF

cat > VERSION << 'VERSION_EOF'
1.0.0
VERSION_EOF

cat > README.md << 'README_EOF'
# magnum-opus-trading

Trading consumers for the magnum-opus data pipeline.

## What this is

Consumer services that read from the pipeline:
- **Dashboard** — Dash/Plotly web UI (port 8050)
- **bots/** — Trading bots *(coming soon)*
- **ml/** — ML training and inference *(coming soon)*

## Prerequisites

**[magnum-opus-pipeline](https://github.com/JeppeOEM/magnum-opus-pipeline) must be running** before starting this repo's services. The pipeline exposes:

| Port | Service |
|------|---------|
| 6379 | Redis (tick and candle streams) |
| 9000 | QuestDB HTTP (historical queries) |

## Quick start

```bash
cp .env.example .env
# Edit .env if pipeline is on a different host (default: localhost)
make up
# Dashboard: http://localhost:8050
```

## What this consumes

**Redis streams** (live data):
- `ticks:{exchange}:{symbol}` — raw ticks from aggregator
- `candles:close:{exchange}:{symbol}` — closed OHLCV bars
- `candles:ob:{exchange}:{symbol}` — order book feature bars

**QuestDB tables** (historical data):
- `snapshot_1s` — 1-second bars with 67 microstructure features
- `snapshot_1m` — 1-minute bars
- `snapshot_15m` — 15-minute bars

## Contract validation

Before deploying, validate the pipeline contract is intact:
```bash
python3 scripts/validate-contract.py
```

## Adding a new consumer

1. Drop your service in a subdirectory (e.g. `bots/my-bot/`)
2. Add a service block to `docker-compose.yml` with `network_mode: host`
3. Read `REDIS_URL` and `QUESTDB_HTTP_ADDR` from environment
README_EOF

# ── Copy validate-contract.py from pipeline ───────────────────────────────────
mkdir -p scripts
if [ -f "$MONOREPO_DIR/scripts/validate-contract.py" ]; then
  cp "$MONOREPO_DIR/scripts/validate-contract.py" scripts/validate-contract.py
  log "Copied validate-contract.py from monorepo"
else
  log "WARNING: validate-contract.py not found in monorepo scripts/ — add manually"
fi

# ── Create GitHub Actions CI ──────────────────────────────────────────────────
mkdir -p .github/workflows
cat > .github/workflows/ci.yml << 'CI_EOF'
name: CI

on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]

jobs:
  dashboard-syntax:
    name: Dashboard — syntax check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
          cache: pip
          cache-dependency-path: dashboard/requirements.txt
      - run: pip install -r dashboard/requirements.txt
      - name: Syntax check
        run: |
          python3 -m py_compile dashboard/app.py \
            dashboard/callbacks.py \
            dashboard/callbacks_bots.py \
            dashboard/callbacks_chart.py \
            dashboard/callbacks_ml.py \
            dashboard/data.py \
            dashboard/bot_data.py \
            dashboard/backtest_data.py
CI_EOF

# ── Initial commit ────────────────────────────────────────────────────────────
git add .
git commit -m "chore: initialize trading repo skeleton

- dashboard/ extracted from monorepo with full git history
- docker-compose.yml (network_mode: host, no pipeline services)
- Makefile (up/down/logs/dashboard/test/validate-contract)
- .env.example (REDIS_URL + QUESTDB_HTTP_ADDR)
- README.md (what this is, prerequisites, quick start)
- scripts/validate-contract.py (contract validation)
- .github/workflows/ci.yml (dashboard syntax check)
- bots/ and ml/ placeholder directories"

# ── Done ──────────────────────────────────────────────────────────────────────
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Trading repo created at: $TRADING_REPO_DIR"
log ""
log "Next steps:"
log "  1. Review the extracted repo:"
log "       cd $TRADING_REPO_DIR && git log --oneline | head -10"
log ""
log "  2. Create GitHub repo (empty, private):"
log "       gh repo create ${GITHUB_USER}/magnum-opus-trading --private"
log ""
log "  3. Push to GitHub:"
log "       cd $TRADING_REPO_DIR"
log "       git remote add origin git@github.com:${GITHUB_USER}/magnum-opus-trading.git"
log "       git push -u origin main"
log ""
log "  4. Test dashboard connects to pipeline:"
log "       cd $TRADING_REPO_DIR && cp .env.example .env && make up"
log "       open http://localhost:8050"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
