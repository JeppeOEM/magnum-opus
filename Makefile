VERSION   := $(shell git describe --tags --always 2>/dev/null || echo dev)
GIT_SHA   := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

export VERSION GIT_SHA BUILD_TIME

.PHONY: up down logs

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f aggregator
