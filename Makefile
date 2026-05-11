SHELL      := /bin/bash
VERSION    := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
REPORTS    := test-results

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs watch monitoring-logs \
        run dev dev-infra dev-infra-down dev-aggregator dev-candle dev-bot dev-gateway dev-frontend \
        test test-l1 test-l2 test-l3 test-l4 test-candle test-chain test-all

## Spin up all services including one paper-trading bot — filtered logs by default, VERBOSE=1 for raw JSON
## Defaults to candle-blue slot; override with SLOT=green
## No API keys needed for paper trading — copy bot-service/.env.example to bot-service/.env as-is
up:
	@printf "\n  %-14s %s\n"  "aggregator"    "http://localhost:8080   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (blue)"  "http://localhost:8081   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (green)" "http://localhost:8082   /health /metrics"
	@printf   "  %-14s %s\n"  "bot"            "http://localhost:8090   /health /metrics"
	@printf   "  %-14s %s\n"  "questdb"        "http://localhost:9000   (ILP: 9009)"
	@printf   "  %-14s %s\n"  "grafana"        "http://localhost:3000"
	@printf   "  %-14s %s\n"  "prometheus"     "http://localhost:9090"
	@printf   "  %-14s %s\n\n" "alertmanager"  "http://localhost:9093"
	@set -o pipefail; \
	if [ "$(VERBOSE)" = "1" ]; then \
		docker compose --profile candle-$(or $(SLOT),blue) --profile bot up --build; \
	else \
		docker compose --profile candle-$(or $(SLOT),blue) --profile bot up --build 2>&1 | python3 scripts/logfmt.py; \
	fi; \
	EXIT=$${PIPESTATUS[0]}; \
	if [ $$EXIT -ne 0 ] && [ $$EXIT -ne 130 ]; then \
		printf "\n\033[31m  ✗ compose exited (code %d)\033[0m\n" $$EXIT; \
		printf "  Hint: docker compose --profile candle-blue --profile bot ps\n"; \
		printf "        docker compose --profile candle-blue --profile bot logs --tail=40\n\n"; \
	fi

## Stop and remove all containers (including test infra)
down:
	docker compose --profile candle-blue --profile candle-green --profile bot down
	docker compose -f docker-compose.test.yml down 2>/dev/null || true

## Tail aggregator logs (when running detached)
logs:
	docker compose logs -f aggregator

## Tail monitoring stack logs (Prometheus, Alertmanager, Grafana)
monitoring-logs:
	docker compose logs -f prometheus alertmanager grafana

## Warnings and errors only — no status line, no INFO noise
watch:
	@printf "\n  %-14s %s\n"  "aggregator"    "http://localhost:8080   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (blue)"  "http://localhost:8081   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (green)" "http://localhost:8082   /health /metrics"
	@printf   "  %-14s %s\n"  "bot"            "http://localhost:8090   /health /metrics"
	@printf   "  %-14s %s\n"  "questdb"        "http://localhost:9000   (ILP: 9009)"
	@printf   "  %-14s %s\n"  "grafana"        "http://localhost:3000"
	@printf   "  %-14s %s\n"  "prometheus"     "http://localhost:9090"
	@printf   "  %-14s %s\n\n" "alertmanager"  "http://localhost:9093"
	@set -o pipefail; \
	docker compose --profile candle-$(or $(SLOT),blue) --profile bot up --build 2>&1 \
	| python3 scripts/logfmt.py --alerts; \
	EXIT=$${PIPESTATUS[0]}; \
	if [ $$EXIT -ne 0 ] && [ $$EXIT -ne 130 ]; then \
		printf "\n\033[31m  ✗ compose exited (code %d)\033[0m\n" $$EXIT; \
		printf "  Hint: docker compose --profile candle-blue --profile bot ps\n"; \
		printf "        docker compose --profile candle-blue --profile bot logs --tail=40\n\n"; \
	fi

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

## Run bot service locally in paper-trading mode (reads bot-service/.env if present)
## Requires bot-service/.venv — run `pip install -r bot-service/requirements.txt` in the venv first
dev-bot:
	@set -a; [ -f bot-service/.env ] && . bot-service/.env; set +a; \
	cd bot-service && \
	LOG_LEVEL=$${LOG_LEVEL:-debug} \
	REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	.venv/bin/uvicorn bot_service.main:app --host 0.0.0.0 --port 8090 --reload

## Run the depthview gateway (WebSocket bridge: Redis pub/sub → browser)
dev-gateway:
	cd gateway && \
	REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	GATEWAY_ADDR=$${GATEWAY_ADDR:-:8083} \
	go run ./cmd/gateway/

## Run the frontend Vite dev server (http://localhost:5173)
## Connects to the gateway at ws://localhost:8083 by default
dev-frontend:
	cd frontend && npm run dev

## Start everything: infra + aggregator + candle + bot + gateway + frontend (Ctrl+C stops all)
run: dev-infra
	@printf "\n  %-14s %s\n"  "aggregator"    "http://localhost:8080"
	@printf   "  %-14s %s\n"  "candle (blue)"  "http://localhost:8081"
	@printf   "  %-14s %s\n"  "bot"            "http://localhost:8090"
	@printf   "  %-14s %s\n"  "gateway"        "ws://localhost:8083"
	@printf   "  %-14s %s\n"  "frontend"       "http://localhost:5173   heatmap: /heatmap.html"
	@printf   "  %-14s %s\n\n" "questdb"       "http://localhost:9000"
	@set -a; [ -f .env ] && . .env; set +a; \
	set -a; [ -f candle-service/.env ] && . candle-service/.env; set +a; \
	set -a; [ -f bot-service/.env ] && . bot-service/.env; set +a; \
	( cd aggregator && \
	  LOG_LEVEL=$${LOG_LEVEL:-info} \
	  REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	  KUCOIN_PUBLIC=$${KUCOIN_PUBLIC:-true} \
	  CONFIG_FILE=../config.yaml \
	  go run ./cmd/aggregator/ ) & AGG=$$!; \
	( cd candle-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-info} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-localhost:9000} \
	  CANDLE_SLOT=$${CANDLE_SLOT:-blue} \
	  go run ./cmd/candle/ ) & CANDLE=$$!; \
	( cd bot-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-info} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	  .venv/bin/uvicorn bot_service.main:app --host 0.0.0.0 --port 8090 ) & BOT=$$!; \
	( cd gateway && \
	  REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	  GATEWAY_ADDR=$${GATEWAY_ADDR:-:8083} \
	  go run ./cmd/gateway/ ) & GATEWAY=$$!; \
	( cd frontend && npm run dev ) & FRONTEND=$$!; \
	trap "kill $$AGG $$CANDLE $$BOT $$GATEWAY $$FRONTEND 2>/dev/null" INT TERM EXIT; \
	wait $$AGG $$CANDLE $$BOT $$GATEWAY $$FRONTEND

## Start infra + all three services (interleaved logs, Ctrl+C stops all)
dev: dev-infra
	@echo "==> aggregator + candle-service + bot (Ctrl+C stops all)"
	@set -a; [ -f .env ] && . .env; set +a; \
	set -a; [ -f candle-service/.env ] && . candle-service/.env; set +a; \
	set -a; [ -f bot-service/.env ] && . bot-service/.env; set +a; \
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
	( cd bot-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-debug} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  QUESTDB_ILP_ADDR=$${QUESTDB_ILP_ADDR:-localhost:9009} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	  .venv/bin/uvicorn bot_service.main:app --host 0.0.0.0 --port 8090 ) & BOT=$$!; \
	trap "kill $$AGG $$CANDLE $$BOT 2>/dev/null" INT TERM EXIT; \
	wait $$AGG $$CANDLE $$BOT
