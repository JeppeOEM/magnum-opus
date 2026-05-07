VERSION    := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA    := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs test test-l1 test-l2 test-l3 test-chain

## Spin up all services and stream logs — Ctrl+C to stop
up:
	docker compose up --build

## Stop and remove containers
down:
	docker compose down

## Tail aggregator logs (when running detached)
logs:
	docker compose logs -f aggregator

## Run L1 + L2 + L3 tests
test:
	$(MAKE) -C aggregator test-all

test-l1:
	$(MAKE) -C aggregator test-l1

test-l2:
	$(MAKE) -C aggregator test-l2

test-l3:
	$(MAKE) -C aggregator test-l3

## Run full chain: L1 + L2 + L3 + L4 (starts and stops Toxiproxy automatically)
test-chain:
	docker compose -f docker-compose.test.yml up -d
	$(MAKE) -C aggregator test-all test-l4; \
	  STATUS=$$?; \
	  docker compose -f docker-compose.test.yml down; \
	  exit $$STATUS
