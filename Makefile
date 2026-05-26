SHELL      := /bin/bash
VERSION    := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
REPORTS    := test-results

export VERSION GIT_SHA BUILD_TIME

# Load VPS connection config if present (sets VPS, VPS_DIR, CANDLE_SLOT)
-include .env.deploy

.PHONY: up down clean logs watch monitoring-logs \
        run dev dev-infra dev-infra-down dev-aggregator dev-candle dev-bot dev-gateway dev-dashboard dev-ml \
        bootstrap setup-vps deploy-all deploy-aggregator deploy-candle deploy-bot deploy-gateway deploy-dashboard deploy-ml deploy-monitoring \
        vps-status vps-logs vps-ssh vps-restart rollback \
        test test-l1 test-l2 test-l3 test-l4 test-candle test-chain test-all test-ml \
        check-data-ready

## Spin up all services including one paper-trading bot — filtered logs by default, VERBOSE=1 for raw JSON
## Defaults to candle-blue slot; override with SLOT=green
## No API keys needed for paper trading — copy bot-service/.env.example to bot-service/.env as-is
up:
	@printf "\n  %-14s %s\n"  "aggregator"    "http://localhost:8080   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (blue)"  "http://localhost:8081   /health /metrics"
	@printf   "  %-14s %s\n"  "candle (green)" "http://localhost:8082   /health /metrics"
	@printf   "  %-14s %s\n"  "bot"            "http://localhost:8090   /health /metrics"
	@printf   "  %-14s %s\n"  "ml-service"     "http://localhost:8000   /health /docs"
	@printf   "  %-14s %s\n"  "questdb"        "http://localhost:9000   (ILP: 9009)"
	@printf   "  %-14s %s\n"  "dashboard"      "http://localhost:8050"
	@printf   "  %-14s %s\n"  "grafana"        "http://localhost:3000"
	@printf   "  %-14s %s\n"  "prometheus"     "http://localhost:9090"
	@printf   "  %-14s %s\n\n" "alertmanager"  "http://localhost:9093"
	@{ until curl -sf http://localhost:8050/_dash-layout >/dev/null 2>&1; do sleep 2; done; \
	   xdg-open http://localhost:8050 2>/dev/null || open http://localhost:8050 2>/dev/null; \
	   printf "  \033[36m→\033[0m dashboard opened in browser\n"; } &
	@set -o pipefail; \
	if [ "$(VERBOSE)" = "1" ]; then \
		docker compose --profile candle-$(or $(SLOT),blue) --profile bot --profile dashboard up --build; \
	else \
		docker compose --profile candle-$(or $(SLOT),blue) --profile bot --profile dashboard up --build 2>&1 | python3 scripts/logfmt.py; \
	fi; \
	EXIT=$${PIPESTATUS[0]}; \
	if [ $$EXIT -ne 0 ] && [ $$EXIT -ne 130 ]; then \
		printf "\n\033[31m  ✗ compose exited (code %d)\033[0m\n" $$EXIT; \
		printf "  Hint: docker compose --profile candle-blue --profile bot ps\n"; \
		printf "        docker compose --profile candle-blue --profile bot logs --tail=40\n\n"; \
	fi

## Stop and remove all containers (including test infra)
down:
	docker compose --profile candle-blue --profile candle-green --profile bot --profile dashboard down
	docker compose -f docker-compose.test.yml down 2>/dev/null || true

## ⚠ DESTRUCTIVE — stop all containers AND delete all data volumes (QuestDB + Redis).
## Requires typing "yes" to confirm. Use only when you want a clean slate.
clean:
	@printf "\n  \033[1;31m⚠  WARNING: This will permanently delete all data!\033[0m\n"
	@printf "  Volumes to be removed:\n"
	@printf "    • magnum-opus_questdb-data   (all candle history, trades, backtests)\n"
	@printf "    • magnum-opus_questdb-conf\n"
	@printf "    • magnum-opus_redis-data     (all streams)\n"
	@printf "    • all other magnum-opus_* volumes (grafana, prometheus, loki, ml)\n\n"
	@printf "  Type \033[1myes\033[0m to continue, anything else to abort: "; \
	read CONFIRM; \
	if [ "$$CONFIRM" != "yes" ]; then \
		printf "\n  Aborted.\n\n"; \
		exit 1; \
	fi
	@printf "\n  Stopping all containers...\n"
	@docker compose --profile candle-blue --profile candle-green --profile bot --profile dashboard down 2>/dev/null || true
	@docker compose -f docker-compose.test.yml down 2>/dev/null || true
	@printf "  Removing data volumes...\n"
	@docker compose --profile candle-blue --profile candle-green --profile bot --profile dashboard down --volumes 2>/dev/null || true
	@printf "\n  \033[32m✓ All containers and data volumes removed.\033[0m\n"
	@printf "  Run \033[1mmake up\033[0m to start fresh.\n\n"

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
	@printf   "  %-14s %s\n"  "ml-service"     "http://localhost:8000   /health /docs"
	@printf   "  %-14s %s\n"  "questdb"        "http://localhost:9000   (ILP: 9009)"
	@printf   "  %-14s %s\n"  "dashboard"      "http://localhost:8050"
	@printf   "  %-14s %s\n"  "grafana"        "http://localhost:3000"
	@printf   "  %-14s %s\n"  "prometheus"     "http://localhost:9090"
	@printf   "  %-14s %s\n\n" "alertmanager"  "http://localhost:9093"
	@set -o pipefail; \
	docker compose --profile candle-$(or $(SLOT),blue) --profile bot --profile dashboard up --build 2>&1 \
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

## ML service tests
test-ml:
	cd ml-service && .venv/bin/pytest tests/ --tb=short -q

## All services in sequence — aggregator (L1+L2+L3 with 95% gate), candle-service (L1+L2), ml-service
test-all:
	@echo "══════════════════════════════════════════════════"
	@echo "  aggregator"
	@echo "══════════════════════════════════════════════════"
	$(MAKE) -C aggregator test-all
	@echo "══════════════════════════════════════════════════"
	@echo "  candle-service"
	@echo "══════════════════════════════════════════════════"
	$(MAKE) -C candle-service test-cover
	@echo "══════════════════════════════════════════════════"
	@echo "  ml-service"
	@echo "══════════════════════════════════════════════════"
	$(MAKE) test-ml

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

## Run the Dash dashboard (http://localhost:8050; requires local Redis + QuestDB)
## Requires dashboard/.venv — run `python3 -m venv dashboard/.venv && dashboard/.venv/bin/pip install -r dashboard/requirements.txt`
dev-dashboard:
	cd dashboard && \
	QUESTDB_HTTP_ADDR=http://localhost:9000 \
	REDIS_URL=redis://localhost:6379 \
	ML_SERVICE_URL=http://localhost:8000 \
	.venv/bin/python app.py

## Run the ML service locally (http://localhost:8000; requires local Redis + QuestDB)
## Requires ml-service/.venv — run `python3 -m venv ml-service/.venv && ml-service/.venv/bin/pip install -e ml-service`
dev-ml:
	@set -a; [ -f ml-service/.env ] && . ml-service/.env; set +a; \
	cd ml-service && \
	LOG_LEVEL=$${LOG_LEVEL:-debug} \
	QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	ML_FEATURE_STORE_PATH=$${ML_FEATURE_STORE_PATH:-./data/features} \
	ML_MODEL_REGISTRY_PATH=$${ML_MODEL_REGISTRY_PATH:-./data/models} \
	.venv/bin/uvicorn ml_service.main:app --host 0.0.0.0 --port 8000 --reload

## Start backend services: infra + aggregator + candle + bot + gateway (dashboard: make dev-dashboard)
run: dev-infra
	@printf "\n  %-14s %s\n"  "aggregator"    "http://localhost:8080"
	@printf   "  %-14s %s\n"  "candle (blue)"  "http://localhost:8081"
	@printf   "  %-14s %s\n"  "bot"            "http://localhost:8090"
	@printf   "  %-14s %s\n"  "ml-service"     "http://localhost:8000"
	@printf   "  %-14s %s\n"  "gateway"        "ws://localhost:8083"
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
	( cd ml-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-info} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  ML_FEATURE_STORE_PATH=$${ML_FEATURE_STORE_PATH:-./data/features} \
	  ML_MODEL_REGISTRY_PATH=$${ML_MODEL_REGISTRY_PATH:-./data/models} \
	  .venv/bin/uvicorn ml_service.main:app --host 0.0.0.0 --port 8000 ) & ML=$$!; \
	( cd gateway && \
	  REDIS_ADDR=$${REDIS_ADDR:-localhost:6379} \
	  GATEWAY_ADDR=$${GATEWAY_ADDR:-:8083} \
	  go run ./cmd/gateway/ ) & GATEWAY=$$!; \
	trap "kill $$AGG $$CANDLE $$BOT $$ML $$GATEWAY 2>/dev/null" INT TERM EXIT; \
	wait $$AGG $$CANDLE $$BOT $$ML $$GATEWAY

## Start infra + all three services (interleaved logs, Ctrl+C stops all)
dev: dev-infra
	@echo "==> aggregator + candle-service + bot + ml-service (Ctrl+C stops all)"
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
	( cd ml-service && \
	  LOG_LEVEL=$${LOG_LEVEL:-debug} \
	  QUESTDB_HTTP_ADDR=$${QUESTDB_HTTP_ADDR:-http://localhost:9000} \
	  REDIS_URL=$${REDIS_URL:-redis://localhost:6379} \
	  ML_FEATURE_STORE_PATH=$${ML_FEATURE_STORE_PATH:-./data/features} \
	  ML_MODEL_REGISTRY_PATH=$${ML_MODEL_REGISTRY_PATH:-./data/models} \
	  .venv/bin/uvicorn ml_service.main:app --host 0.0.0.0 --port 8000 ) & ML=$$!; \
	trap "kill $$AGG $$CANDLE $$BOT $$ML 2>/dev/null" INT TERM EXIT; \
	wait $$AGG $$CANDLE $$BOT $$ML

# ── VPS Deploy ────────────────────────────────────────────────────────────────
# All targets read VPS and VPS_DIR from .env.deploy (auto-loaded above).
# Create .env.deploy from .env.deploy.example before using these targets.

## One-time VPS bootstrap — runs bootstrap-vps.sh on the VPS as root
##   Usage: make bootstrap VPS=root@<IP>
bootstrap:
	@[ -n "$(VPS)" ] || (echo "Usage: make bootstrap VPS=root@<IP>"; exit 1)
	ssh $(VPS) 'bash -s' < scripts/bootstrap-vps.sh

## First-time VPS setup — guided step-by-step provisioning
##   Usage: make setup-vps VPS_IP=45.56.78.90
setup-vps:
	@[ -n "$(VPS_IP)" ] || (echo "Usage: make setup-vps VPS_IP=<public-ip>"; exit 1)
	bash scripts/setup-vps.sh $(VPS_IP)

## Deploy all services in sequence (infra → aggregator → candle → gateway → bot → dashboard → monitoring)
deploy-all:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh all

## Deploy aggregator service
deploy-aggregator:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh aggregator

## Deploy candle service (blue-green, zero-downtime)
deploy-candle:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh candle

## Deploy bot service (warns if open positions, never blocks)
deploy-bot:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh bot

## Deploy gateway service
deploy-gateway:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh gateway

## Deploy dashboard service
deploy-dashboard:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh dashboard

## Deploy ML service
deploy-ml:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh ml

## Deploy monitoring stack (prometheus / alertmanager / loki / promtail / grafana)
deploy-monitoring:
	@[ -n "$(VPS)" ] || (echo "VPS not set — create .env.deploy from .env.deploy.example"; exit 1)
	bash scripts/vps-deploy.sh monitoring

## Roll back a service to its previous image after a failed deploy
##   Usage: make rollback SERVICE=bot
rollback:
	@[ -n "$(VPS)" ] || (echo "VPS not set"; exit 1)
	@[ -n "$(SERVICE)" ] || (echo "Usage: make rollback SERVICE=<name>"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && \
	  docker tag magnum-opus-$(SERVICE):rollback magnum-opus-$(SERVICE):latest && \
	  docker compose up -d --no-deps $(SERVICE)"
	@echo "✓ $(SERVICE) rolled back to previous image"

## Show status of all services on VPS
vps-status:
	@[ -n "$(VPS)" ] || (echo "VPS not set"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && docker compose ps"

## Tail logs for a specific service on VPS
##   Usage: make vps-logs SERVICE=bot
vps-logs:
	@[ -n "$(VPS)" ] || (echo "VPS not set"; exit 1)
	@[ -n "$(SERVICE)" ] || (echo "Usage: make vps-logs SERVICE=<name>"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && docker compose logs --tail=100 -f $(SERVICE)"

## Check if snapshot_1s data meets ML training readiness criteria
## Usage: make check-data-ready SYMBOL=BTCUSDT EXCHANGE=bybit
check-data-ready:
	python3 scripts/data_readiness_gate.py --symbol $(SYMBOL) --exchange $(or $(EXCHANGE),bybit)

# ── Pipeline repo (magnum-opus-pipeline) ──────────────────────────────────────

## Start pipeline-only services (aggregator + candle-blue + redis + questdb, no consumers)
pipeline-up:
	VERSION=$(VERSION) GIT_SHA=$(GIT_SHA) BUILD_TIME=$(BUILD_TIME) \
	docker compose -f docker-compose.pipeline.yml --profile candle-blue up -d --build
	@echo ""
	@echo "  aggregator   http://localhost:8080/health"
	@echo "  candle-blue  http://localhost:8081/health"
	@echo "  questdb      http://localhost:9000"
	@echo "  redis        localhost:6379"
	@echo ""
	@echo "  Consumer repos: REDIS_URL=redis://localhost:6379  QUESTDB_URL=http://localhost:9000"

## Stop pipeline-only services
pipeline-down:
	docker compose -f docker-compose.pipeline.yml \
	  --profile candle-blue --profile candle-green --profile monitoring down

## Validate pipeline ↔ trading contract (requires pipeline running)
validate-contract:
	python3 scripts/validate-contract.py

## Tag and release a new pipeline version
## Usage: make release VERSION=1.1.0
release:
	@[ -n "$(VERSION)" ] || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	@printf "%s\n" "$(VERSION)" > VERSION
	git add VERSION
	git commit -m "chore: release pipeline-v$(VERSION)"
	git tag "pipeline-v$(VERSION)"
	@echo ""
	@echo "  ✓ Tagged pipeline-v$(VERSION)"
	@echo "  Run: git push origin main --tags"

## Extract pipeline repo using git-filter-repo (creates /tmp/magnum-opus-pipeline)
setup-pipeline-repo:
	bash scripts/setup-pipeline-repo.sh

## Extract trading repo using git-filter-repo (creates /tmp/magnum-opus-trading)
setup-trading-repo:
	bash scripts/setup-trading-repo.sh

## SSH into the VPS
vps-ssh:
	@[ -n "$(VPS)" ] || (echo "VPS not set"; exit 1)
	ssh $(VPS)

## Restart a specific service on VPS without rebuilding
##   Usage: make vps-restart SERVICE=bot
vps-restart:
	@[ -n "$(VPS)" ] || (echo "VPS not set"; exit 1)
	@[ -n "$(SERVICE)" ] || (echo "Usage: make vps-restart SERVICE=<name>"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && docker compose restart $(SERVICE)"
