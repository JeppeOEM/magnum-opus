SHELL      := /bin/bash
VERSION    := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
REPORTS    := test-results

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs \
        dev dev-infra dev-infra-down dev-aggregator dev-candle \
        test test-l1 test-l2 test-l3 test-l4 test-candle test-chain test-all

## Spin up all services — filtered logs by default, VERBOSE=1 for raw JSON
up:
	@if [ "$(VERBOSE)" = "1" ]; then \
		docker compose up --build; \
	else \
		docker compose up --build 2>&1 | python3 scripts/logfmt.py; \
	fi

## Stop and remove containers
down:
	docker compose down

## Tail aggregator logs (when running detached)
logs:
	docker compose logs -f aggregator

## L1 tests — pure functions, coverage gate
test-l1:
	./scripts/test-report.sh "L1" $(REPORTS)/l1.md $(MAKE) -C aggregator test-l1

## L2 tests — mock infrastructure
test-l2:
	./scripts/test-report.sh "L2" $(REPORTS)/l2.md $(MAKE) -C aggregator test-l2

## L3 tests — mock WebSocket
test-l3:
	./scripts/test-report.sh "L3" $(REPORTS)/l3.md $(MAKE) -C aggregator test-l3

## L4 tests — fault injection (requires Toxiproxy running)
test-l4:
	./scripts/test-report.sh "L4" $(REPORTS)/l4.md $(MAKE) -C aggregator test-l4

## L1 + L2 + L3 (no infrastructure needed)
test:
	./scripts/test-report.sh "L1+L2+L3" $(REPORTS)/l123.md $(MAKE) -C aggregator test-all

## Full chain: L1 + L2 + L3 + L4 (starts and stops Toxiproxy automatically)
test-chain:
	docker compose -f docker-compose.test.yml up -d
	./scripts/test-report.sh "Full Chain (L1–L4)" $(REPORTS)/chain.md \
	  $(MAKE) -C aggregator test-all test-l4; \
	STATUS=$$?; \
	docker compose -f docker-compose.test.yml down; \
	exit $$STATUS

## Candle service L1 + L2 tests
test-candle:
	$(MAKE) -C candle-service test-all

## Both services in sequence — aggregator (L1+L2+L3 with 95% gate) then candle-service (L1+L2 with coverage summary)
test-all:
	@echo "══════════════════════════════════════════════════"
	@echo "  aggregator"
	@echo "══════════════════════════════════════════════════"
	$(MAKE) -C aggregator test-all
	@echo "══════════════════════════════════════════════════"
	@echo "  candle-service"
	@echo "══════════════════════════════════════════════════"
	$(MAKE) -C candle-service test-cover

# ── Dev ───────────────────────────────────────────────────────────────────────

## Start Redis + QuestDB in Docker (detached)
dev-infra:
	docker compose up -d redis questdb
	@echo "Redis and QuestDB are up (localhost:6379, localhost:9000, localhost:9009)"

## Stop Redis + QuestDB
dev-infra-down:
	docker compose stop redis questdb
	docker compose rm -f redis questdb

## Run aggregator locally (reads .env if present)
dev-aggregator:
	@set -a; [ -f .env ] && . .env; set +a; \
	cd aggregator && \
	LOG_LEVEL=$${LOG_LEVEL:-debug} \
	REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	KUCOIN_PUBLIC=$${KUCOIN_PUBLIC:-true} \
	CONFIG_FILE=../config.yaml \
	go run ./cmd/aggregator/

## Run candle service locally, blue slot (reads candle-service/.env if present)
dev-candle:
	@set -a; [ -f candle-service/.env ] && . candle-service/.env; set +a; \
	cd candle-service && \
	LOG_LEVEL=$${LOG_LEVEL:-debug} \
	REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	CANDLE_SLOT=$${CANDLE_SLOT:-blue} \
	go run ./cmd/candle/

## Start infra + both services (interleaved logs, Ctrl+C stops all)
dev: dev-infra
	@echo "==> aggregator + candle-service (Ctrl+C stops both)"
	@set -a; [ -f .env ] && . .env; set +a; \
	set -a; [ -f candle-service/.env ] && . candle-service/.env; set +a; \
	( cd aggregator && \
	  LOG_LEVEL=$${LOG_LEVEL:-debug} \
	  REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	  KUCOIN_PUBLIC=$${KUCOIN_PUBLIC:-true} \
	  CONFIG_FILE=../config.yaml \
	  go run ./cmd/aggregator/ ) & AGG=$$!; \
	( cd candle-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-debug} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	  CANDLE_SLOT=$${CANDLE_SLOT:-blue} \
	  go run ./cmd/candle/ ) & CANDLE=$$!; \
	trap "kill $$AGG $$CANDLE 2>/dev/null" INT TERM EXIT; \
	wait $$AGG $$CANDLE
