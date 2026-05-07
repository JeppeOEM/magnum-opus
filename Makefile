VERSION    := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
REPORTS    := test-results

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs test test-l1 test-l2 test-l3 test-l4 test-chain

## Spin up all services and stream logs — Ctrl+C to stop
up:
	docker compose up --build

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
